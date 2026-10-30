"""Normalise a raw inbound email into a canonical ``InboundEmail`` envelope.

Deterministic Python (no LLM), run inside the agent as the ``normalize_email`` tool: the S3 Trigger Lambda is not changed. It handles
two shapes: a web form with labelled lines, and a direct or threaded email. It strips banners, the legal footer and quoted history
to isolate the latest message, counts attachment markers, and extracts a real-looking policy number for the identity lookup
(placeholders like "[POLICY NUMBER]" are treated as absent).

ILLUSTRATIVE: the markers below are made up for the demo insurer; an insurer's real mail shapes replace them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ATTACHMENT_MARKER = re.compile(r"\[attachment[^\]]*\]", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_POLICY_RE = re.compile(r"\bPOL-[A-Za-z0-9-]+\b", re.IGNORECASE)
_POLICY_LABEL_RE = re.compile(r"policy\s*(?:number|no\.?|#)\s*:?\s*([A-Za-z0-9-]{3,})", re.IGNORECASE)

_BANNER_MARKERS = ("WARNING:", "This email originated from outside", "DO NOT click links")
_FOOTER_START = "Illustrative Insurer Pty Ltd"
_TRIGGER_PREFIX_RE = re.compile(r"^\s*process this[^\n:]*:\s*", re.IGNORECASE)
_FORM_LABELS = {
    "email address": "email",
    "policy number": "policy_number",
    "reason for enquiry": "reason",
    "message": "message",
}
_THIRD_PARTY_MARKERS = (
    "on behalf of",
    "financial counsellor",
    "financial counselor",
    "authorised representative",
    "authorized representative",
    "our client",
    "solicitor",
    "power of attorney",
)


@dataclass
class InboundEmail:
    channel: str = "direct_email"  # web_form | direct_email | third_party
    sender_type: str = "unknown"  # policyholder | third_party | unknown
    from_email: str | None = None
    subject: str = ""
    policy_number: str | None = None  # raw value as seen (may be a placeholder)
    policy_number_for_lookup: str | None = None  # real-looking number, else None
    form_reason: str | None = None  # web-form hint only
    latest_message: str = ""
    attachment_count: int = 0
    sender_authenticated: bool = False  # a DMARC pass reported by the mail path; False unless told
    raw: str = ""


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    v = value.strip()
    return v.startswith("[") and v.endswith("]")


def _strip_banner_and_footer(text: str) -> str:
    idx = text.find(_FOOTER_START)
    if idx != -1:
        text = text[:idx]
    kept = [line for line in text.splitlines() if not any(line.strip().startswith(m) for m in _BANNER_MARKERS)]
    return "\n".join(kept)


def _latest_message(text: str) -> str:
    """The content above the first quoted-thread marker."""
    markers = [
        re.compile(r"^\s*From:\s*$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*From:\s*\S+.*<", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*-{3,}\s*Original Message", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^On .+wrote:\s*$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*Sent:\s", re.IGNORECASE | re.MULTILINE),
    ]
    cut = len(text)
    for rx in markers:
        m = rx.search(text)
        if m and m.start() < cut:
            cut = m.start()
    head = text[:cut].strip()
    return head or text.strip()  # a reply with only quoted history falls back to the whole text


def _parse_form(text: str) -> dict[str, str]:
    """Labelled lines ("Policy number: X"). The message runs from its label to the end."""
    out: dict[str, str] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        label, sep, value = line.partition(":")
        key = _FORM_LABELS.get(label.strip().lower())
        if not sep or key is None:
            continue
        if key == "message":
            out[key] = "\n".join([value.strip(), *lines[i + 1 :]]).strip()
            break
        out[key] = value.strip()
    return out


def _find_policy_number(*texts: str) -> str | None:
    for text in texts:
        m = _POLICY_RE.search(text or "")
        if m and not _is_placeholder(m.group(0)):
            return m.group(0).upper()
    for text in texts:
        m = _POLICY_LABEL_RE.search(text or "")
        if m and not _is_placeholder(m.group(1)):
            return m.group(1)
    return None


def _detect_third_party(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in _THIRD_PARTY_MARKERS)


def normalize_email(
    raw: str,
    *,
    sender_email: str | None = None,
    source: str | None = None,
    subject: str | None = None,
    sender_authenticated: bool = False,
) -> InboundEmail:
    """Turn raw email text (as forwarded by the Trigger Lambda) into an InboundEmail."""
    raw = raw or ""
    text = _TRIGGER_PREFIX_RE.sub("", raw, count=1)

    inbound = InboundEmail(raw=raw, subject=subject or "", sender_authenticated=bool(sender_authenticated))
    inbound.attachment_count = len(_ATTACHMENT_MARKER.findall(text))

    fields = _parse_form(text)
    is_form = "policy_number" in fields and "message" in fields

    if is_form:
        inbound.channel = "web_form"
        inbound.form_reason = fields.get("reason") or None
        inbound.policy_number = fields.get("policy_number") or None
        inbound.from_email = sender_email or (fields.get("email") or None)
        inbound.latest_message = fields["message"]
        inbound.sender_type = "policyholder"
    else:
        cleaned = _strip_banner_and_footer(text)
        inbound.latest_message = _latest_message(cleaned)
        inbound.from_email = sender_email
        if _detect_third_party(cleaned):
            inbound.channel = "third_party"
            inbound.sender_type = "third_party"
        else:
            inbound.sender_type = "policyholder"

    if _is_placeholder(inbound.from_email):
        m = _EMAIL_RE.search(text)
        inbound.from_email = m.group(0) if m else None

    inbound.policy_number_for_lookup = _find_policy_number(inbound.policy_number or "", inbound.latest_message, text)
    return inbound
