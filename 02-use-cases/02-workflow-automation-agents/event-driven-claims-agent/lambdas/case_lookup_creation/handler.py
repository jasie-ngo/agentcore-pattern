import hashlib
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("HESTA_CASES_TABLE", "Hesta-cases"))

_ACTIVE_STATUSES = {"open", "pending", "in_progress", "in progress", "active"}
_MAX_HISTORY_ENTRIES = 30
_MAX_HISTORY_ENTRY_BYTES = 8_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _idempotency_key(event: dict) -> str:
    explicit = event.get("idempotency_key")
    if explicit:
        return str(explicit)
    source = event.get("source_object_id") or event.get("source") or ""
    message = event.get("inbound_email") or ""
    return hashlib.sha256(f"{source}\n{message}".encode("utf-8")).hexdigest()


def _case_id(member_id: str, intent_id: str, idempotency_key: str) -> str:
    value = hashlib.sha256(f"{member_id}\n{intent_id}\n{idempotency_key}".encode("utf-8")).hexdigest()
    return f"CASE-{value[:20].upper()}"


def _history_entry(entry_type: str, content: str, entry_id: str, runtime_version: str) -> dict:
    entry = {
        "entry_id": entry_id,
        "entry_type": entry_type,
        "content": content[:_MAX_HISTORY_ENTRY_BYTES],
        "timestamp": _now(),
        "pipeline_version": runtime_version,
    }
    return entry


def _append_history(case_id: str, entries: list[dict], retries: int = 1) -> dict:
    """Append entries atomically; a duplicate entry_id is a successful no-op."""
    item = table.get_item(Key={"case_id": case_id}).get("Item")
    if not item:
        return {"error": "Case not found"}

    history = item.get("conversation_history", [])
    known_ids = {entry.get("entry_id") for entry in history}
    new_entries = [entry for entry in entries if entry.get("entry_id") not in known_ids]
    if not new_entries:
        return {"status": "history_unchanged", "case": item, "case_id": case_id}

    kept = (history + new_entries)[-_MAX_HISTORY_ENTRIES:]
    try:
        response = table.update_item(
            Key={"case_id": case_id},
            UpdateExpression="SET conversation_history = :history, history_revision = :next_revision",
            ConditionExpression="attribute_not_exists(history_revision) OR history_revision = :revision",
            ExpressionAttributeValues={
                ":history": kept,
                ":revision": item.get("history_revision", 0),
                ":next_revision": item.get("history_revision", 0) + 1,
            },
            ReturnValues="ALL_NEW",
        )
        updated = response.get("Attributes", {**item, "conversation_history": kept})
        updated["history_revision"] = item.get("history_revision", 0) + 1
        return {"status": "history_appended", "case": updated, "case_id": case_id}
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            if retries:
                return _append_history(case_id, entries, retries=retries - 1)
            raise
        raise


def handler(event, context):
    event = event or {}

    # History updates are also used after drafting and do not perform a new lookup.
    if event.get("case_id") and event.get("history_entries"):
        return _append_history(event["case_id"], event["history_entries"])

    member_id = event.get("member_id")
    if not member_id:
        return {"error": "verified member_id required; case lookup skipped"}

    intent_id = event.get("primary_intent_id") or "other_unknown"
    idempotency_key = _idempotency_key(event)
    response = table.query(
        IndexName="member_id-index",
        KeyConditionExpression=Key("member_id").eq(member_id),
    )
    items = response.get("Items", [])

    # An exact event replay wins before status/intent matching.
    replay = next((item for item in items if item.get("idempotency_key") == idempotency_key), None)
    if replay:
        return {
            "status": "existing_cases_found",
            "case_id": replay["case_id"],
            "cases": [replay],
            "conversation_history": replay.get("conversation_history", []),
        }

    matching_candidates = [
        item
        for item in items
        if item.get("primary_intent_id") == intent_id
        and str(item.get("status", "")).lower() in _ACTIVE_STATUSES
    ]
    matching = sorted(
        matching_candidates,
        key=lambda item: (item.get("created_at", ""), item.get("case_id", "")),
    )[0] if matching_candidates else None
    if matching:
        return {
            "status": "existing_cases_found",
            "case_id": matching["case_id"],
            "cases": [matching],
            "conversation_history": matching.get("conversation_history", []),
        }

    case_id = _case_id(member_id, intent_id, idempotency_key)
    runtime_version = event.get("pipeline_version", "hesta-v2")
    inbound = event.get("inbound_email")
    history = []
    if inbound:
        history.append(
            _history_entry(
                "inbound_member_email",
                str(inbound),
                f"{idempotency_key}:inbound",
                runtime_version,
            )
        )

    new_case = {
        "case_id": case_id,
        "member_id": member_id,
        "primary_intent_id": intent_id,
        "idempotency_key": idempotency_key,
        "status": "Open",
        "identity_status": "verified",
        "conversation_history": history,
        "history_revision": 0,
        "created_at": _now(),
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
        return {
            "status": "existing_cases_found",
            "case_id": case_id,
            "cases": [existing],
            "conversation_history": existing.get("conversation_history", []),
        }

    return {
        "status": "new_case_created",
        "case_id": case_id,
        "case": new_case,
        "conversation_history": history,
    }
