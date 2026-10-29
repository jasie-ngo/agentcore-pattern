"""Agent blueprints — common agent roles, filled in from a use case's config.yaml.

Most pipelines reuse the same few roles; only the content differs. A use case
names the role and fills in the content; the blueprint writes the system prompt
and generates the structured-output schema, so no prompt engineering or
``schemas.py`` is needed for these agents:

    agents:
      intent_classifier:
        type: ../_shared/agent_blueprints.py:classifier
        model: fast
        agent_kwargs:
          task: "Classify why a customer is contacting us."
          labels:
            - { id: card_dispute, description: "...", signals: ["don't recognise"] }

| Blueprint    | Role                                              | Form fields                         |
|--------------|---------------------------------------------------|-------------------------------------|
| classifier   | what is this about? (labels + confidence)         | labels, flags, fields, multi_label, fallback |
| extractor    | pull named fields out of text                     | fields                              |
| assessor     | gather evidence with tools, choose an outcome     | outcomes, criteria, facts, fallback_outcome |
| reviewer     | independent second check against a checklist      | checks                              |
| writer       | draft a reply in house style from approved text   | style, knowledge, rules, signoff    |

Every blueprint also takes ``task`` (one sentence), ``context`` (who the agent
works for), ``extra_instructions`` (anything domain-specific the generic prompt
misses) and ``gateway: true`` (attach the Gateway tools). A ``system_prompt:``
written in the YAML is appended as further instructions. When a role does not
fit, use ``../_shared/factory.py:agent`` with a hand-written prompt instead.

Field types in forms: string | number | integer | boolean | list (of strings),
plus ``enum: [...]`` to restrict a string to listed values.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Literal

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pydantic import BaseModel, Field, create_model

_TYPES: dict[str, Any] = {"string": str, "number": float, "integer": int, "boolean": bool, "list": list[str]}


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _pascal(name: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", name or "agent") if w)


def _literal(values: list[str], where: str):
    if not values:
        raise ValueError(f"{where}: needs at least one value")
    return Literal[tuple(values)]  # type: ignore[valid-type]


def _field_type(spec: dict, where: str):
    if spec.get("enum"):
        return _literal([str(v) for v in spec["enum"]], f"{where}.enum")
    type_name = spec.get("type", "string")
    if type_name not in _TYPES:
        raise ValueError(f"{where}: type must be one of {sorted(_TYPES)}, got '{type_name}'")
    return _TYPES[type_name]


def _optional_fields(fields: dict[str, dict], where: str) -> dict[str, Any]:
    """Form fields → pydantic fields that may be missing (None)."""
    out: dict[str, Any] = {}
    for name, spec in (fields or {}).items():
        spec = spec or {}
        out[name] = (
            _field_type(spec, f"{where}.{name}") | None,
            Field(None, description=spec.get("description", name)),
        )
    return out


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _prompt(*sections: str | None) -> str:
    return "\n\n".join(s.strip() for s in sections if s and s.strip())


def _header(kwargs: dict, default_task: str) -> str:
    context = kwargs.pop("context", "")
    task = kwargs.pop("task", default_task)
    return _prompt(context, task)


def _footer(kwargs: dict) -> str:
    extra = kwargs.pop("extra_instructions", "")
    yaml_prompt = kwargs.pop("system_prompt", None) or ""
    return _prompt(
        f"Additional instructions:\n{extra}" if extra else "",
        f"Further instructions:\n{yaml_prompt}" if yaml_prompt else "",
        "Never invent facts that are not in the input or returned by your tools.",
    )


def _from_spec(spec, kwargs: dict):
    """Build the agent: the spec turns the form into (model, prompt); the rest goes to Agent."""
    from _shared.factory import build_agent  # local: keeps prompt/schema building importable without strands

    name = kwargs.pop("name", spec.__name__.removesuffix("_spec"))
    model, prompt, rest = spec(name, **kwargs)
    return build_agent(structured_output_model=model, system_prompt=prompt, name=name, **rest)


# ─── classifier ────────────────────────────────────────────────────────────────


def classifier_spec(
    name: str,
    *,
    labels: list[dict],
    flags: dict | None = None,
    fields: dict | None = None,
    multi_label: bool = True,
    fallback: str = "other",
    **kwargs,
) -> tuple[type[BaseModel], str, dict]:
    """Return (output model, system prompt, remaining kwargs) for a classifier."""
    where = f"agent {name} (classifier)"
    if not labels:
        raise ValueError(f"{where}: labels is required")
    ids = [str(label["id"]) for label in labels]
    if fallback not in ids:
        ids.append(fallback)
    label_type = _literal(ids, f"{where}.labels")

    intent_model = create_model(
        f"{_pascal(name)}Label",
        id=(label_type, Field(description="Label id")),
        confidence=(int, Field(ge=0, le=100, description="Confidence 0-100")),
        rationale=(str, Field(description="One short sentence why")),
    )
    model_fields: dict[str, Any] = {
        "labels": (list[intent_model], Field(description="Every label that applies, highest confidence first")),
        "primary_id": (label_type, Field(description="The single most likely label")),
        "needs_triage": (bool, Field(description="True when ambiguous, conflicting or unsure")),
    }
    for flag, description in (flags or {}).items():
        model_fields[flag] = (bool, Field(description=description))
    model_fields.update(_optional_fields(fields or {}, f"{where}.fields"))
    model = create_model(f"{_pascal(name)}Result", **model_fields)

    taxonomy = []
    for label in labels:
        lines = [f"- id: {label['id']}" + (f" — {label['description']}" if label.get("description") else "")]
        if label.get("signals"):
            lines.append(f"    signals: {'; '.join(label['signals'])}")
        for example in label.get("examples", []):
            lines.append(f'    example: "{example}"')
        taxonomy.append("\n".join(lines))

    rules = [
        "Return EVERY label that applies, each with its own confidence (0-100), highest first."
        if multi_label
        else "Return exactly one label.",
        "Set primary_id to the single most likely label.",
        f'If nothing matches confidently, use "{fallback}" and set needs_triage true.',
        "Set needs_triage true when the message is ambiguous, conflicting, or you are unsure.",
        "Classify from meaning; placeholders such as [NUMBER] carry no meaning.",
    ]
    flag_rules = [f"{flag}: true only when {description}" for flag, description in (flags or {}).items()]
    field_rules = [
        f"{field}: {(spec or {}).get('description', field)}"
        + (f" (one of: {', '.join(map(str, spec['enum']))})" if (spec or {}).get("enum") else "")
        for field, spec in (fields or {}).items()
    ]
    prompt = _prompt(
        _header(kwargs, "Classify the message."),
        "Choose from this fixed set of labels:\n" + "\n".join(taxonomy),
        "Rules:\n" + _bullets(rules),
        ("Also set these flags (independent of the label):\n" + _bullets(flag_rules)) if flag_rules else "",
        ("Also fill in these fields (leave one empty when the message does not say):\n" + _bullets(field_rules))
        if field_rules
        else "",
        _footer(kwargs),
    )
    return model, prompt, kwargs


def classifier(**kwargs):
    """Agent blueprint: classify a message into a fixed set of labels."""
    return _from_spec(classifier_spec, kwargs)


# ─── extractor ─────────────────────────────────────────────────────────────────


def extractor_spec(name: str, *, fields: dict[str, dict], **kwargs) -> tuple[type[BaseModel], str, dict]:
    where = f"agent {name} (extractor)"
    if not fields:
        raise ValueError(f"{where}: fields is required")
    model = create_model(
        f"{_pascal(name)}Result",
        **_optional_fields(fields, where),
        missing=(list[str], Field(default_factory=list, description="Fields the text does not state")),
        notes=(str, Field("", description="Anything a human should double-check")),
    )
    described = [
        f"{field}: {(spec or {}).get('description', field)}"
        + (f" (one of: {', '.join(map(str, spec['enum']))})" if (spec or {}).get("enum") else "")
        for field, spec in fields.items()
    ]
    prompt = _prompt(
        _header(kwargs, "Extract the listed fields from the message."),
        "Fields:\n" + _bullets(described),
        "Rules:\n"
        + _bullets(
            [
                "Take values only from the message. Leave a field empty (null) when it is not stated.",
                "List every field you left empty in missing.",
                "Numbers are plain numbers without currency symbols or thousands separators.",
            ]
        ),
        _footer(kwargs),
    )
    return model, prompt, kwargs


def extractor(**kwargs):
    """Agent blueprint: extract named fields from text."""
    return _from_spec(extractor_spec, kwargs)


# ─── assessor ──────────────────────────────────────────────────────────────────


def assessor_spec(
    name: str,
    *,
    outcomes: list,
    criteria: list[str] | None = None,
    facts: dict | None = None,
    fallback_outcome: str | None = None,
    **kwargs,
) -> tuple[type[BaseModel], str, dict]:
    where = f"agent {name} (assessor)"
    outcome_ids = [str(o["id"] if isinstance(o, dict) else o) for o in outcomes or []]
    outcome_type = _literal(outcome_ids, f"{where}.outcomes")
    if fallback_outcome and fallback_outcome not in outcome_ids:
        raise ValueError(f"{where}: fallback_outcome '{fallback_outcome}' is not one of the outcomes")
    model = create_model(
        f"{_pascal(name)}Result",
        outcome=(outcome_type, Field(description="The assessment outcome")),
        confidence=(int, Field(ge=0, le=100, description="Confidence in the outcome 0-100")),
        reasons=(list[str], Field(description="The evidence behind the outcome, one point each")),
        **_optional_fields(facts or {}, f"{where}.facts"),
    )
    described_outcomes = [
        f"{o['id']}: {o.get('when', '')}".rstrip(": ") if isinstance(o, dict) else str(o) for o in outcomes
    ]
    rules = [
        "Gather the evidence first: call the tools you need before deciding.",
        "Base the outcome only on the input and on what your tools returned.",
        "Record every fact you established in the matching field; leave unknown facts empty (null).",
    ]
    if fallback_outcome:
        rules.append(f'If a tool fails or the evidence is insufficient, choose "{fallback_outcome}" and say why.')
    prompt = _prompt(
        _header(kwargs, "Assess the case and choose one outcome."),
        "Possible outcomes:\n" + _bullets(described_outcomes),
        ("Assess against these criteria:\n" + _bullets(criteria)) if criteria else "",
        "Rules:\n" + _bullets(rules),
        _footer(kwargs),
    )
    return model, prompt, kwargs


def assessor(**kwargs):
    """Agent blueprint: gather evidence (usually via Gateway tools) and choose an outcome."""
    return _from_spec(assessor_spec, kwargs)


# ─── reviewer ──────────────────────────────────────────────────────────────────


def reviewer_spec(name: str, *, checks: dict[str, str], **kwargs) -> tuple[type[BaseModel], str, dict]:
    where = f"agent {name} (reviewer)"
    if not checks:
        raise ValueError(f"{where}: checks is required")
    for check in checks:
        if not re.match(r"^[a-z][a-z0-9_]*$", check):
            raise ValueError(f"{where}: check names must be snake_case, got '{check}'")
    model = create_model(
        f"{_pascal(name)}Result",
        **{check: (bool, Field(description=description)) for check, description in checks.items()},
        approved=(bool, Field(description="True only if every check passes")),
        confidence=(int, Field(ge=0, le=100, description="Confidence in this review 0-100")),
        issues=(list[str], Field(default_factory=list, description="Each problem found")),
    )
    prompt = _prompt(
        _header(kwargs, "Independently review the work you are given."),
        "Checks (each true or false):\n" + _bullets([f"{c}: {d}" for c, d in checks.items()]),
        "Rules:\n"
        + _bullets(
            [
                "Review independently; do not assume the earlier step was right.",
                "approved is true only if EVERY check is true.",
                "List each failed check or concern in issues.",
            ]
        ),
        _footer(kwargs),
    )
    return model, prompt, kwargs


def reviewer(**kwargs):
    """Agent blueprint: independent second check against a checklist."""
    return _from_spec(reviewer_spec, kwargs)


# ─── writer ────────────────────────────────────────────────────────────────────


class Draft(BaseModel):
    """A drafted reply — for a human to review and send."""

    subject: str = Field(description="Reply subject line")
    body: str = Field(description="Reply body")
    assumptions: list[str] = Field(default_factory=list, description="Anything a human should confirm before sending")


def writer_spec(
    name: str,
    *,
    style: list[str] | str = "",
    knowledge: dict[str, str] | None = None,
    rules: list[str] | None = None,
    signoff: str = "",
    **kwargs,
) -> tuple[type[BaseModel], str, dict]:
    style_lines = [style] if isinstance(style, str) and style else list(style or [])
    knowledge_text = "\n".join(f"[{topic}]\n{text.strip()}" for topic, text in (knowledge or {}).items())
    base_rules = [
        "Draft only. You are not sending anything; a human reviews the draft.",
        "Use only the approved knowledge below and the facts you are given. Do not invent amounts, dates, outcomes or policy.",
        "Never promise an outcome the facts do not confirm.",
    ]
    prompt = _prompt(
        _header(kwargs, "Draft a reply to the customer."),
        ("House style:\n" + _bullets(style_lines)) if style_lines else "",
        "Rules:\n" + _bullets(base_rules + list(rules or [])),
        f"Approved knowledge (pick the topic that matches):\n{knowledge_text}" if knowledge_text else "",
        f"Sign off with:\n{signoff}" if signoff else "",
        _footer(kwargs),
    )
    return Draft, prompt, kwargs


def writer(**kwargs):
    """Agent blueprint: draft a reply in house style from approved knowledge."""
    return _from_spec(writer_spec, kwargs)
