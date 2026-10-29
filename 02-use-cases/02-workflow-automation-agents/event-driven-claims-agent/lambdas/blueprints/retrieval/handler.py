"""Blueprint: DynamoDB retrieval tool.

One copy of this code backs every ``blueprint: retrieval`` entry in any use
case's ``gateway_tools.yaml``. Nothing here is specific to a table or a domain:
each deployed copy is told what to do by two settings that CDK sets from the
generated manifest (``agentcore/generated/manifest.json``):

    TABLE_NAME   the DynamoDB table to read
    TOOL_CONFIG  JSON — this tool's entry from gateway_tools.yaml:
        lookup   list of {key, index?, type?}: try each key the caller supplied,
                 in order; ``index`` means "query that GSI", otherwise GetItem.
                 ``type: number`` converts the value to a number first.
        filter   {index, key, value | from_input, limit?, newest_first?}: list
                 mode — return every record where key == value (via the GSI).
                 ``value`` is fixed; ``from_input: true`` takes it from the
                 caller's ``key`` argument instead. ``where`` lists more fields
                 the caller supplies; only records whose fields equal them are
                 kept. ``limit`` caps the count (applied before ``where``);
                 ``newest_first`` sorts by the index's sort key, descending.
                 Used instead of ``lookup``.
        returns  the only fields handed back (keeps unrelated data, e.g. PII,
                 away from the agent).
        result_key  list mode only — name of the list in the response (default "items").

Responses (Gateway convention: plain ``json.dumps``, no HTTP envelope):

    found:      {"found": true, <returned fields>}
    list:       {"found": <bool>, "count": n, "<result_key>": [...]}
    not found:  {"found": false, "error": "Record not found for key=value"}
    bad input:  {"found": false, "error": "..."}
"""

import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

CONFIG = json.loads(os.environ.get("TOOL_CONFIG", "{}"))
table = boto3.resource("dynamodb").Table(os.environ.get("TABLE_NAME", ""))


def _project(item: dict) -> dict:
    fields = CONFIG.get("returns")
    return {f: item.get(f) for f in fields} if fields else dict(item)


def _coerce(value, value_type: str | None):
    return Decimal(str(value)) if value_type == "number" else value


def _dump(payload: dict) -> str:
    return json.dumps(payload, default=str)


def _list(event: dict) -> str:
    flt = CONFIG["filter"]
    value = event.get(flt["key"]) if flt.get("from_input") else flt.get("value")
    if value in (None, ""):
        return _dump({"found": False, "error": f"{flt['key']} is required"})
    query = {
        "IndexName": flt["index"],
        "KeyConditionExpression": Key(flt["key"]).eq(_coerce(value, flt.get("type"))),
        "ScanIndexForward": not flt.get("newest_first", False),
    }
    where = None
    for field in flt.get("where", []):
        if event.get(field) in (None, ""):
            return _dump({"found": False, "error": f"{field} is required"})
        condition = Attr(field).eq(event[field])
        where = condition if where is None else where & condition
    if where is not None:
        query["FilterExpression"] = where
    if flt.get("limit"):
        query["Limit"] = int(flt["limit"])
    items = [_project(i) for i in table.query(**query).get("Items", [])]
    return _dump({"found": bool(items), "count": len(items), CONFIG.get("result_key", "items"): items})


def handler(event, context):
    event = event or {}
    if CONFIG.get("filter"):
        return _list(event)

    lookups = CONFIG.get("lookup", [])
    for lookup in lookups:
        key = lookup["key"]
        value = event.get(key)
        if value in (None, ""):
            continue
        value = _coerce(value, lookup.get("type"))
        if lookup.get("index"):
            items = table.query(IndexName=lookup["index"], KeyConditionExpression=Key(key).eq(value)).get("Items", [])
            item = items[0] if items else None
        else:
            item = table.get_item(Key={key: value}).get("Item")
        if item:
            return _dump({"found": True, **_project(item)})
        return _dump({"found": False, "error": f"Record not found for {key}={value}"})

    keys = " or ".join(lookup["key"] for lookup in lookups) or "a lookup key"
    return _dump({"found": False, "error": f"{keys} is required"})
