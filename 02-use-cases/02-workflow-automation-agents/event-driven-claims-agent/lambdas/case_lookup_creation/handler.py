import hashlib
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("HESTA_CASES_TABLE", "Hesta-cases"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case_id(member_id: str, thread_key: str) -> str:
    """Deterministic per-(member, email thread) case id.

    A case is now identified by the verified member id + the normalized email subject
    (thread_key) — a member can have several concurrent cases, one per thread. Deterministic
    so concurrent first contacts on the same thread still collide on the same conditional
    put_item and resolve to one case, rather than creating two.
    """
    value = hashlib.sha256(f"{member_id}\n{thread_key}".encode("utf-8")).hexdigest()
    return f"CASE-{value[:20].upper()}"


# The only two persisted statuses. "valid" wins forever once set (D7); a non-"valid" status
# sent for a form_id is stored as this generic tag — enough for staff visibility ("a form did
# arrive, it just wasn't usable") without needing to persist every intermediate reason.
_RECEIVED_NOT_VALID = "received_not_valid"


def _update_case_facts(case_id: str, case_facts: dict) -> dict:
    """Update last_intent_id/last_contact_at and any forms_on_file entries.

    The primary update (last_intent_id, last_contact_at, and any *valid* forms) is a single,
    unconditional ``update_item`` with no prior read — writing "valid" is idempotent regardless
    of the form's previous status, so it's always race-free. ``forms_on_file`` always exists on
    a case (initialised to ``{}`` at creation), so the nested path is always valid to SET into.

    A non-valid form status is recorded as ``received_not_valid`` via its OWN small conditional
    update, separate from the primary one — its ConditionExpression refuses to downgrade a
    form that's already "valid" (a member re-sending a stale/broken copy after successfully
    validating must never appear to un-validate it). That check is scoped to just this one
    attribute, so it can never block the primary update (last_intent_id/last_contact_at/other
    forms) from landing, and needs no prior read either — DynamoDB evaluates the condition
    against the item as it stands at write time.
    """
    set_clauses = ["last_contact_at = :now"]
    expr_names: dict = {}
    expr_values = {":now": _now()}

    last_intent_id = case_facts.get("last_intent_id")
    if last_intent_id:
        set_clauses.append("last_intent_id = :last_intent_id")
        expr_values[":last_intent_id"] = last_intent_id

    forms_update = case_facts.get("forms_on_file") or {}
    valid_form_ids = [form_id for form_id, status in forms_update.items() if status == "valid"]
    non_valid_forms = [(form_id, status) for form_id, status in forms_update.items() if status != "valid"]

    for i, form_id in enumerate(valid_form_ids):
        name_key, value_key = f"#f{i}", f":f{i}"
        expr_names[name_key] = form_id
        expr_values[value_key] = "valid"
        set_clauses.append(f"forms_on_file.{name_key} = {value_key}")

    kwargs = {
        "Key": {"case_id": case_id},
        "UpdateExpression": "SET " + ", ".join(set_clauses),
        "ConditionExpression": "attribute_exists(case_id)",
        "ExpressionAttributeValues": expr_values,
        "ReturnValues": "ALL_NEW",
    }
    if expr_names:
        kwargs["ExpressionAttributeNames"] = expr_names

    try:
        response = table.update_item(**kwargs)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"error": "Case not found"}
        raise
    updated = response.get("Attributes", {})

    for form_id, _status in non_valid_forms:
        try:
            response = table.update_item(
                Key={"case_id": case_id},
                UpdateExpression="SET forms_on_file.#fid = :status",
                ConditionExpression="attribute_not_exists(forms_on_file.#fid) OR forms_on_file.#fid <> :valid",
                ExpressionAttributeNames={"#fid": form_id},
                ExpressionAttributeValues={":status": _RECEIVED_NOT_VALID, ":valid": "valid"},
                ReturnValues="ALL_NEW",
            )
            updated = response.get("Attributes", updated)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            # Already "valid" — leave it alone; this is the intended outcome, not a failure.

    return {"status": "facts_updated", "case": updated, "case_id": case_id}


def handler(event, context):
    event = event or {}

    # Case-facts updates (after drafting) do not perform a new lookup.
    if event.get("case_id") and event.get("case_facts"):
        return _update_case_facts(event["case_id"], event["case_facts"])

    member_id = event.get("member_id")
    if not member_id:
        return {"error": "verified member_id required; case lookup skipped"}

    thread_key = event.get("thread_key")
    if thread_key is None:
        return {"error": "thread_key required; case lookup skipped"}

    case_id = _case_id(member_id, thread_key)
    existing = table.get_item(Key={"case_id": case_id}).get("Item")
    if existing:
        return {"status": "existing_case_found", "case_id": case_id, "case": existing}

    intent_id = event.get("primary_intent_id") or "other_unknown"
    now = _now()
    new_case = {
        "case_id": case_id,
        "member_id": member_id,
        "thread_key": thread_key,
        "subject": event.get("subject") or "",
        "status": "Open",
        "identity_status": "verified",
        "primary_intent_id": intent_id,
        "last_intent_id": intent_id,
        "created_at": now,
        "last_contact_at": now,
        "forms_on_file": {},
    }
    if event.get("sender_email"):
        new_case["sender_email"] = event["sender_email"]

    try:
        table.put_item(
            Item=new_case,
            ConditionExpression="attribute_not_exists(case_id)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(Key={"case_id": case_id}).get("Item")
        if not existing:
            raise
        return {"status": "existing_case_found", "case_id": case_id, "case": existing}

    return {"status": "new_case_created", "case_id": case_id, "case": new_case}
