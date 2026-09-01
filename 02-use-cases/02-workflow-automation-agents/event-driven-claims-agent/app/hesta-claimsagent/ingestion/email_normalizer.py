"""Normalise a raw inbound "email" into a canonical ``InboundEmail`` envelope.

This is deterministic Python (no LLM) and runs INSIDE the agent — the S3 Trigger
Lambda is NOT modified (per the plan's reuse decisions). It handles the two shapes
seen in the HESTA samples:

  A. Contact-Us web form  — "You've received a new form based mail … Values: …"
  B. Direct / threaded email — WARNING banner, [ATTACHMENT FILENAME] markers,
     quoted From:/Sent:/Subject: history, and the HESTA legal footer.

It strips banners/footers/quoted history to isolate the *latest* member message,
parses contact-form fields, counts attachment markers, and extracts a real-looking
member/policy number for the identity lookup (placeholders like "[MEMBER NUMBER]"
are treated as absent).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Contact-form field labels, in the order they appear in the samples.
_FORM_LABELS = [
    "enquiry-sent-from",
    "email-address",
    "member-number",
    "name",
    "phone",
    "reason-for-enquiry",
    "message",
]

_ATTACHMENT_MARKER = re.compile(r"\[attachment[^\]]*\]", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A real member/policy number: e.g. POL-12345, or a bare 5-9 digit run.
_POLICY_RE = re.compile(r"\bPOL-[A-Za-z0-9-]+\b", re.IGNORECASE)
_MEMBER_LABEL_RE = re.compile(
    r"member(?:ship)?\s*(?:number|no\.?|#)\s*:?\s*([A-Za-z0-9-]{3,})", re.IGNORECASE
)
_DIGIT_RUN_RE = re.compile(r"\b\d{5,9}\b")

# Lines that are pure noise (security banner).
_BANNER_MARKERS = (
    "WARNING:",
    "This email originated from outside",
    "DO NOT click links",
)
# Everything from here on is the standard HESTA legal footer — drop it.
_FOOTER_START = "Issued by H.E.S.T"
# A leading instruction the current Trigger Lambda may prepend.
_TRIGGER_PREFIX_RE = re.compile(r"^\s*process this[^\n:]*:\s*", re.IGNORECASE)


@dataclass
class InboundEmail:
    channel: str = "direct_email"  # contact_form | direct_email | third_party
    sender_type: str = "unknown"  # member | non_member | solicitor | unknown
    from_email: str | None = None
    subject: str = ""
    member_number: str | None = None  # raw value as seen (may be a placeholder)
    member_number_for_lookup: str | None = None  # real-looking number, else None
    form_reason: str | None = None  # contact-form hint only
    latest_message: str = ""
    attachment_count: int = 0
    raw: str = ""


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    v = value.strip()
    return v.startswith("[") and v.endswith("]")


def _strip_banner_and_footer(text: str) -> str:
    # Drop the legal footer entirely.
    idx = text.find(_FOOTER_START)
    if idx != -1:
        text = text[:idx]
    # Drop banner lines.
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(m) for m in _BANNER_MARKERS):
            continue
        kept.append(line)
    return "\n".join(kept)


def _latest_message(text: str) -> str:
    """Return the content above the first quoted-thread marker."""
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
    # If the "latest" turns out empty (e.g. reply with only quoted history), fall back to full text.
    return head or text.strip()


def _parse_contact_form(text: str) -> dict[str, str]:
    idx = text.find("Values:")
    seg = text[idx + len("Values:") :] if idx != -1 else text
    positions = []
    for label in _FORM_LABELS:
        m = re.search(re.escape(label), seg)
        if m:
            positions.append((m.start(), m.end(), label))
    positions.sort()
    out: dict[str, str] = {}
    for i, (_start, end, label) in enumerate(positions):
        nxt = positions[i + 1][0] if i + 1 < len(positions) else len(seg)
        value = seg[end:nxt]
        # strip leading separators and zero-width characters the form uses
        value = value.strip(" \t\r\n:\u2060\u200b\ufeff\u00a0")
        out[label] = value
    return out


def _find_member_number(*texts: str) -> str | None:
    for text in texts:
        if not text:
            continue
        m = _POLICY_RE.search(text)
        if m and not _is_placeholder(m.group(0)):
            return m.group(0).upper()
    for text in texts:
        if not text:
            continue
        m = _MEMBER_LABEL_RE.search(text)
        if m and not _is_placeholder(m.group(1)):
            return m.group(1)
    for text in texts:
        if not text:
            continue
        m = _DIGIT_RUN_RE.search(text)
        if m:
            return m.group(0)
    return None


def _detect_solicitor(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "we refer to the above matter",
        "our office",
        "solicitor",
        "law firm",
        "lawyers",
        "family law",
        "dear sir/madam",
    )
    return any(m in lowered for m in markers)


def normalize_email(
    raw: str, *, sender_email: str | None = None, source: str | None = None, subject: str | None = None
) -> InboundEmail:
    """Turn raw email text (as forwarded by the Trigger Lambda) into an InboundEmail."""
    raw = raw or ""
    text = _TRIGGER_PREFIX_RE.sub("", raw, count=1)

    inbound = InboundEmail(raw=raw, subject=subject or "")
    inbound.attachment_count = len(_ATTACHMENT_MARKER.findall(text))

    is_form = "form based mail" in text.lower() or "Values:" in text

    if is_form:
        inbound.channel = "contact_form"
        fields = _parse_contact_form(text)
        inbound.form_reason = fields.get("reason-for-enquiry") or None
        inbound.member_number = fields.get("member-number") or None
        inbound.from_email = sender_email or (fields.get("email-address") or None)
        inbound.latest_message = fields.get("message") or _latest_message(text)
        enquiry_from = (fields.get("enquiry-sent-from") or "").lower()
        if "non-member" in enquiry_from:
            inbound.sender_type = "non_member"
        elif "member" in enquiry_from:
            inbound.sender_type = "member"
    else:
        cleaned = _strip_banner_and_footer(text)
        inbound.latest_message = _latest_message(cleaned)
        inbound.from_email = sender_email
        if _detect_solicitor(cleaned):
            inbound.channel = "third_party"
            inbound.sender_type = "solicitor"
        else:
            inbound.sender_type = "member"

    # Fall back to any email address found in the body if the sender is still unknown.
    if _is_placeholder(inbound.from_email):
        m = _EMAIL_RE.search(text)
        inbound.from_email = m.group(0) if m else None

    inbound.member_number_for_lookup = _find_member_number(
        inbound.member_number or "", inbound.latest_message, text
    )
    return inbound
