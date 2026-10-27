"""Generic, config-driven normaliser for inbound messages (emails, web forms, text).

Turns raw inbound text into the framework's standard **envelope** — one dict
shape every use case receives, whichever channel the message came through.
The code is use-case-neutral; everything that differs between businesses
(legal footer, security banner, web-form field names, how a customer id looks,
sender-type markers) comes from that use case's ``ingestion.yaml``.

Used in two places, so it must stay **standard-library only**:

* the Trigger Lambda (``lambdas/trigger``) — normalises every S3 upload before
  the agent is invoked; CDK bundles this file into the Lambda at build time;
* each use case's ``normalize_email`` tool — the fallback when an agent is
  invoked directly with raw text (``test_invoke.py``, ``sca dev``).

Envelope fields:

    envelope_version   1
    use_case           which use case's ingestion config was applied
    source             where the message came from (e.g. s3://bucket/key)
    channel            contact_form | direct_email | third_party
    sender_type        e.g. member | non_member | solicitor | unknown
    from_email         sender address (header, form field, or first address found)
    sender_name        sender display name, when a form provides one
    subject            subject line, when known
    id_raw             customer id exactly as written (may be a placeholder)
    id_for_lookup      a real-looking customer id, else None ("[MEMBER NUMBER]" → None)
    form_reason        the web form's "reason" field, when present
    latest_message     the newest message only — banners, footers, quoted history removed
    attachment_count   number of [ATTACHMENT …] markers
    raw_text           the original text (truncated), for agents that need history
"""

from __future__ import annotations

import re

ENVELOPE_VERSION = 1
RAW_TEXT_LIMIT = 20_000

# Generic email conventions — not business-specific, so they live in code.
_ATTACHMENT_MARKER = r"\[attachment[^\]]*\]"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_QUOTED_HISTORY_MARKERS = [
    re.compile(r"^\s*From:\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*From:\s*\S+.*<", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*-{3,}\s*Original Message", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^On .+wrote:\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Sent:\s", re.IGNORECASE | re.MULTILINE),
]
# A leading instruction older callers prepend ("Process this claim: …").
_INSTRUCTION_PREFIX_RE = re.compile(r"^\s*process this[^\n:]*:\s*", re.IGNORECASE)
_FORM_VALUE_STRIP = " \t\r\n:\u2060\u200b\ufeff\u00a0"


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    v = value.strip()
    return v.startswith("[") and v.endswith("]")


def _strip_banner_and_footer(text: str, config: dict) -> str:
    for marker in config.get("footer_markers", []):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    banners = tuple(config.get("banner_markers", []))
    if not banners:
        return text
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith(banners))


def _latest_message(text: str) -> str:
    """Return the content above the first quoted-thread marker."""
    cut = len(text)
    for rx in _QUOTED_HISTORY_MARKERS:
        m = rx.search(text)
        if m and m.start() < cut:
            cut = m.start()
    head = text[:cut].strip()
    # A reply holding only quoted history falls back to the full text.
    return head or text.strip()


def _is_form(text: str, form: dict) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in form.get("detect", []))


def _parse_form(text: str, form: dict) -> dict[str, str]:
    """Split ``label value label value …`` after the values marker into a dict."""
    values_marker = form.get("values_marker", "")
    idx = text.find(values_marker) if values_marker else -1
    seg = text[idx + len(values_marker) :] if idx != -1 else text
    positions = []
    for label in form.get("labels", []):
        m = re.search(re.escape(label), seg)
        if m:
            positions.append((m.start(), m.end(), label))
    positions.sort()
    out: dict[str, str] = {}
    for i, (_start, end, label) in enumerate(positions):
        nxt = positions[i + 1][0] if i + 1 < len(positions) else len(seg)
        out[label] = seg[end:nxt].strip(_FORM_VALUE_STRIP)
    return out


def _find_id(patterns: list[dict], *texts: str) -> str | None:
    """First match of the first pattern that matches any text (pattern order wins)."""
    for spec in patterns:
        rx = re.compile(spec["regex"], re.IGNORECASE)
        group = spec.get("group", 0)
        for text in texts:
            if not text:
                continue
            m = rx.search(text)
            if m and not _is_placeholder(m.group(group)):
                value = m.group(group)
                return value.upper() if spec.get("uppercase") else value
    return None


def normalize(
    raw: str,
    config: dict | None = None,
    *,
    use_case: str = "",
    sender_email: str | None = None,
    subject: str | None = None,
    source: str | None = None,
) -> dict:
    """Normalise raw inbound text into the standard envelope.

    Args:
        raw: The message text as received (headers already removed when the
            caller parsed them into ``sender_email``/``subject``).
        config: The use case's ``ingestion.yaml`` as a dict. Empty/None applies
            only the generic email conventions.
        use_case: Recorded on the envelope.
        sender_email: Sender address from the message headers, if known.
        subject: Subject line from the message headers, if known.
        source: Where the message came from.

    Returns:
        The envelope dict (see module docstring).
    """
    config = config or {}
    raw = raw or ""
    text = _INSTRUCTION_PREFIX_RE.sub("", raw, count=1)
    form_cfg = config.get("form") or {}
    form_fields = form_cfg.get("fields", {})

    env: dict = {
        "envelope_version": ENVELOPE_VERSION,
        "use_case": use_case,
        "source": source or "",
        "channel": "direct_email",
        "sender_type": "unknown",
        "from_email": sender_email or None,
        "sender_name": None,
        "subject": subject or "",
        "id_raw": None,
        "id_for_lookup": None,
        "form_reason": None,
        "latest_message": "",
        "attachment_count": len(re.findall(config.get("attachment_marker", _ATTACHMENT_MARKER), text, re.IGNORECASE)),
        "raw_text": raw[:RAW_TEXT_LIMIT],
    }

    if form_cfg and _is_form(text, form_cfg):
        env["channel"] = "contact_form"
        fields = _parse_form(text, form_cfg)

        def field(name: str) -> str | None:
            label = form_fields.get(name)
            return (fields.get(label) or None) if label else None

        env["form_reason"] = field("reason")
        env["id_raw"] = field("id")
        env["sender_name"] = field("name")
        env["from_email"] = sender_email or field("sender_email")
        env["latest_message"] = field("message") or _latest_message(text)
        kind = (field("sender_kind") or "").lower()
        for rule in form_cfg.get("sender_kinds", []):
            if rule["contains"].lower() in kind:
                env["sender_type"] = rule["type"]
                break
    else:
        cleaned = _strip_banner_and_footer(text, config)
        env["latest_message"] = _latest_message(cleaned)
        lowered = cleaned.lower()
        for rule in config.get("sender_types", []):
            if any(marker.lower() in lowered for marker in rule.get("markers", [])):
                env["sender_type"] = rule["type"]
                env["channel"] = rule.get("channel", env["channel"])
                break
        else:
            env["sender_type"] = config.get("default_sender_type", "unknown")

    # Fall back to any address in the body if the sender is still unknown.
    if _is_placeholder(env["from_email"]):
        m = _EMAIL_RE.search(text)
        env["from_email"] = m.group(0) if m else None

    env["id_for_lookup"] = _find_id(config.get("id_patterns", []), env["id_raw"] or "", env["latest_message"], text)
    return env


def load_config(path) -> dict:
    """Load an ``ingestion.yaml`` (agent side only — needs PyYAML)."""
    import yaml  # local import: the Trigger Lambda reads JSON and never calls this

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
