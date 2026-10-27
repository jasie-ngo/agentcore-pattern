"""Blueprint: DynamoDB record-write tool (create, or find-or-create).

One copy of this code backs every ``blueprint: record_write`` entry in any use
case's ``gateway_tools.yaml``. Each deployed copy is told what to do by settings
that CDK sets from the generated manifest (``agentcore/generated/manifest.json``):

    TABLE_NAME        the DynamoDB table to write
    NOTIFY_TOPIC_ARN  optional SNS topic to publish each new record to
    TOOL_CONFIG       JSON — this tool's entry from gateway_tools.yaml:
        inputs     {field: {type, required?, description?}} — the only fields
                   accepted from the caller; ``type`` is string | number |
                   integer | boolean | object | array.
        id         {field, prefix, from?: [fields], length?} — how the record id
                   is made. With ``from``, the id is a hash of those fields, so
                   the same inputs always give the same id; if any of them is
                   missing a random id is used instead. Without ``from``, random.
        if_exists  new_record (default) | return_existing | error — what to do
                   when a record with that id is already there.
        rules      {field: {max_length?, min?, max?, one_of?}} — input checks.
        defaults   {field: value} — used when the caller leaves a field out.
        set        {field: value} — always written, the caller cannot override.
        derived    {field: {from, gte, then, else}} — e.g. priority from amount.
        timestamps [fields] — set to the current UTC time on create.
        notify     {subject} — SNS subject template, e.g. "Review needed: {claim_id}".

Responses (Gateway convention: plain ``json.dumps``, no HTTP envelope):

    created:   {<record fields>, "result": "created"}
    existing:  {<existing record fields>, "result": "existing"}   (if_exists: return_existing)
    rejected:  {"error": "..."}
"""

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3
from botocore.exceptions import ClientError

CONFIG = json.loads(os.environ.get("TOOL_CONFIG", "{}"))
TOPIC_ARN = os.environ.get("NOTIFY_TOPIC_ARN", "")
table = boto3.resource("dynamodb").Table(os.environ.get("TABLE_NAME", ""))
sns = boto3.client("sns") if TOPIC_ARN else None


class _InputError(ValueError):
    pass


def _dump(payload: dict) -> str:
    return json.dumps(payload, default=str)


def _coerce(field: str, value, value_type: str):
    if value_type in ("number", "integer"):
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise _InputError(f"{field} must be a number") from None
        return Decimal(int(number)) if value_type == "integer" else number
    if value_type == "boolean":
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
    return value


def _validate(item: dict) -> None:
    for field, rule in (CONFIG.get("rules") or {}).items():
        value = item.get(field)
        if value is None:
            continue
        if "max_length" in rule and len(str(value)) > rule["max_length"]:
            raise _InputError(f"{field} must be at most {rule['max_length']} characters")
        if "min" in rule and value < Decimal(str(rule["min"])):
            raise _InputError(f"{field} must be at least {rule['min']}")
        if "max" in rule and value > Decimal(str(rule["max"])):
            raise _InputError(f"{field} must be at most {rule['max']}")
        if "one_of" in rule and value not in rule["one_of"]:
            raise _InputError(f"{field} must be one of {', '.join(map(str, rule['one_of']))}")


def _make_id(item: dict) -> str:
    spec = CONFIG.get("id") or {}
    prefix = spec.get("prefix", "")
    sources = spec.get("from") or []
    values = [item.get(f) for f in sources]
    if sources and all(v not in (None, "") for v in values):
        digest = hashlib.sha256("\n".join(str(v) for v in values).encode("utf-8")).hexdigest()
        body = digest[: spec.get("length", 16)].upper()
    else:
        body = uuid.uuid4().hex[: spec.get("length", 8)].upper()
    return f"{prefix}-{body}" if prefix else body


def _build_item(event: dict) -> dict:
    inputs = CONFIG.get("inputs") or {}
    item: dict = {}
    for field, spec in inputs.items():
        value = event.get(field)
        if value in (None, ""):
            continue
        item[field] = _coerce(field, value, spec.get("type", "string"))

    missing = [f for f, spec in inputs.items() if spec.get("required") and f not in item]
    if missing:
        raise _InputError(f"Missing required field(s): {', '.join(missing)}")

    for field, value in (CONFIG.get("defaults") or {}).items():
        item.setdefault(field, value)
    item.update(CONFIG.get("set") or {})
    _validate(item)

    for field, rule in (CONFIG.get("derived") or {}).items():
        source = item.get(rule["from"], Decimal(0))
        item[field] = rule["then"] if source >= Decimal(str(rule["gte"])) else rule["else"]

    now = datetime.now(UTC).isoformat()
    for field in CONFIG.get("timestamps") or []:
        item[field] = now

    id_field = (CONFIG.get("id") or {}).get("field")
    if id_field:
        item[id_field] = _make_id(item)
    return item


def _publish(item: dict) -> None:
    if not (sns and CONFIG.get("notify")):
        return
    subject = CONFIG["notify"].get("subject", "New record").format_map(_Missing(item))
    sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=_dump(item))


class _Missing(dict):
    """format_map helper: unknown {fields} render as empty instead of raising."""

    def __missing__(self, key):
        return ""


def handler(event, context):
    try:
        item = _build_item(event or {})
    except _InputError as exc:
        return _dump({"error": str(exc)})

    mode = CONFIG.get("if_exists", "new_record")
    id_field = (CONFIG.get("id") or {}).get("field")

    if mode == "new_record" or not id_field:
        table.put_item(Item=item)
    else:
        try:
            table.put_item(Item=item, ConditionExpression=f"attribute_not_exists({id_field})")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            if mode == "error":
                return _dump({"error": f"{id_field} {item[id_field]} already exists"})
            existing = table.get_item(Key={id_field: item[id_field]}).get("Item") or item
            return _dump({**existing, "result": "existing"})

    _publish(item)
    return _dump({**item, "result": "created"})
