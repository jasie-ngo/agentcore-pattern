"""Deterministic plain-text form parser (TODO 4).

No LLM, no third-party dependencies — plain string handling over the D2 template
shape:

    HESTA-FORM-ID: <form-id>
    HESTA-FORM-NAME: <name>
    ----------------------------------------------------------
    Label               : value
    ----------------------------------------------------------

``parse_form`` extracts the form id and every ``Label : value`` line into a
dict keyed by the label text exactly as it appears (callers match against
``FormField.label``). ``is_blank`` decides whether a filled-in value actually
counts as filled in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FORM_ID_RE = re.compile(r"^HESTA-FORM-ID:\s*(.+?)\s*$", re.MULTILINE)
_FIELD_LINE_RE = re.compile(r"^([^\n:]+?)\s*:\s*(.*)$")

# A blank field is empty/whitespace, a run of placeholder punctuation (blank-line
# underscores/dashes/dots), or a bracketed placeholder like "[MEMBER NAME]".
_PLACEHOLDER_RUN_RE = re.compile(r"^[_\-.\s]*$")
_BRACKETED_PLACEHOLDER_RE = re.compile(r"^\[.*\]$")


@dataclass(frozen=True)
class ParsedForm:
    form_id: str | None
    fields: dict[str, str]


def is_blank(value: str | None) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    if _PLACEHOLDER_RUN_RE.match(stripped):
        return True
    if _BRACKETED_PLACEHOLDER_RE.match(stripped):
        return True
    return False


def parse_form(text: str) -> ParsedForm | None:
    """Parse a filled (or blank) HESTA form. Returns None if the text has no
    recognisable ``HESTA-FORM-ID`` header — i.e. it isn't a HESTA form at all."""
    if not text:
        return None
    id_match = _FORM_ID_RE.search(text)
    if not id_match:
        return None
    form_id = id_match.group(1).strip()

    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip().startswith("HESTA-FORM-ID") or line.strip().startswith("HESTA-FORM-NAME"):
            continue
        if set(line.strip()) <= {"-"} and line.strip():
            continue  # separator rule
        match = _FIELD_LINE_RE.match(line.strip())
        if match:
            label, value = match.group(1).strip(), match.group(2).strip()
            if label:
                fields[label] = value

    return ParsedForm(form_id=form_id, fields=fields)
