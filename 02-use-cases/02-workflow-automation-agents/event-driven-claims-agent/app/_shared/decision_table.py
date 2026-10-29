"""Decision tables — deterministic business decisions written as a YAML form.

A decision table is an ordered list of rules. The first rule whose ``when``
conditions all hold decides; if none does, ``default`` decides. No model is
involved, so the same inputs always give the same decision, and the rules can be
read and reviewed by the business without reading code.

    name: decide_dispute
    description: Decide what happens to a card dispute.
    inputs:
      amount:          { type: number,  description: "Disputed amount" }
      fraud_signals:   { type: boolean, description: "Assessor found a fraud pattern" }
      customer_status: { type: string,  description: "Account status" }
    rules:
      - id: fraud
        when: { fraud_signals: true }
        then: { outcome: block_card_and_escalate, escalate: true, reason: "Fraud pattern" }
      - id: small_valid
        when: { amount: { lt: 100 }, customer_status: active }
        then: { outcome: provisional_credit, escalate: false, reason: "Small, valid dispute" }
    default: { outcome: human_review, escalate: true, reason: "No rule matched" }

Conditions: a plain value means "equals"; otherwise one or more of
``eq, ne, lt, lte, gt, gte, in, not_in, is_empty, same_as``. ``same_as: <input>``
compares with another input (text compared trimmed and case-insensitive, e.g. a
sender email against the email on record). A missing (or null) input fails every
condition except ``is_empty: true``.

``hit_policy`` picks how rules combine:

* ``first`` (default) — the first matching rule decides (one outcome).
* ``collect`` — every matching rule applies; use it for checklists such as "escalate
  if ANY of these is true, and say every reason". The result is the first match's
  ``then`` plus ``matches`` (each matching rule's ``then`` and ``rule_id``) and
  ``rule_ids``; with no match it is the default, with empty ``matches``.

Standard library only. ``_shared/local_tools.py`` turns a table into an agent tool.
"""

from __future__ import annotations

from typing import Any

_OPERATORS = {"eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "is_empty", "same_as"}
_HIT_POLICIES = {"first", "collect"}
_INPUT_TYPES = {"string", "number", "integer", "boolean"}


def validate(table: dict) -> None:
    """Raise ValueError with a clear message if the table is malformed."""
    name = table.get("name") or "?"
    where = f"decision table {name}"
    if not table.get("name") or not table.get("description"):
        raise ValueError(f"{where}: name and description are required")
    inputs = table.get("inputs") or {}
    for field, spec in inputs.items():
        if (spec or {}).get("type", "string") not in _INPUT_TYPES:
            raise ValueError(f"{where}: inputs.{field}.type must be one of {sorted(_INPUT_TYPES)}")
    if table.get("hit_policy", "first") not in _HIT_POLICIES:
        raise ValueError(f"{where}: hit_policy must be one of {sorted(_HIT_POLICIES)}")
    if "default" not in table or "outcome" not in (table["default"] or {}):
        raise ValueError(f"{where}: a default with an outcome is required")
    seen: set[str] = set()
    for i, rule in enumerate(table.get("rules") or []):
        rule_id = rule.get("id") or f"rules[{i}]"
        if rule_id in seen:
            raise ValueError(f"{where}: duplicate rule id '{rule_id}'")
        seen.add(rule_id)
        if "outcome" not in (rule.get("then") or {}):
            raise ValueError(f"{where}: rule {rule_id} needs then.outcome")
        for field, condition in (rule.get("when") or {}).items():
            if field not in inputs:
                raise ValueError(f"{where}: rule {rule_id} uses '{field}', which is not one of the inputs")
            if isinstance(condition, dict):
                unknown = set(condition) - _OPERATORS
                if unknown:
                    raise ValueError(f"{where}: rule {rule_id} uses unknown operator(s) {sorted(unknown)}")
                if "same_as" in condition and condition["same_as"] not in inputs:
                    raise ValueError(
                        f"{where}: rule {rule_id} compares with '{condition['same_as']}', which is not one of the inputs"
                    )


def _same(a: Any, b: Any) -> bool:
    if b is None or b == "":
        return False
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() == b.strip().casefold()
    return a == b


def _holds(value: Any, condition: Any, values: dict) -> bool:
    if not isinstance(condition, dict):
        condition = {"eq": condition}
    if value is None or value == "":
        return condition.get("is_empty") is True
    for op, expected in condition.items():
        if op == "is_empty":
            ok = not expected
        elif op == "eq":
            ok = value == expected
        elif op == "ne":
            ok = value != expected
        elif op == "in":
            ok = value in expected
        elif op == "not_in":
            ok = value not in expected
        elif op == "same_as":
            ok = _same(value, values.get(expected))
        else:
            try:
                ok = {
                    "lt": value < expected,
                    "lte": value <= expected,
                    "gt": value > expected,
                    "gte": value >= expected,
                }[op]
            except TypeError:
                ok = False
        if not ok:
            return False
    return True


def _coerce(inputs: dict, spec: dict) -> dict:
    out: dict = {}
    for field, field_spec in spec.items():
        value = inputs.get(field)
        kind = (field_spec or {}).get("type", "string")
        try:
            if value is None or value == "":
                out[field] = None
            elif kind == "number":
                out[field] = float(value)
            elif kind == "integer":
                out[field] = int(float(value))
            elif kind == "boolean":
                out[field] = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
            else:
                out[field] = str(value)
        except (TypeError, ValueError):
            out[field] = None
    return out


def evaluate(table: dict, inputs: dict) -> dict:
    """Apply the table to the inputs. Returns the decision and which rule(s) made it."""
    values = _coerce(inputs, table.get("inputs") or {})
    matches = []
    for i, rule in enumerate(table.get("rules") or []):
        if all(_holds(values.get(field), cond, values) for field, cond in (rule.get("when") or {}).items()):
            match = {**rule["then"], "rule_id": rule.get("id") or f"rules[{i}]"}
            if table.get("hit_policy", "first") == "first":
                return {**match, "inputs": values}
            matches.append(match)
    if not matches:
        result = {**table["default"], "rule_id": "default", "inputs": values}
        return {**result, "matches": [], "rule_ids": []} if table.get("hit_policy") == "collect" else result
    return {**matches[0], "matches": matches, "rule_ids": [m["rule_id"] for m in matches], "inputs": values}
