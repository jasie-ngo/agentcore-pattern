"""AI-012 — Reviewer & Editor.

Checks the Writer's draft for accuracy, tone and compliance before a human sends it.
It does not send; it produces a review a human can act on alongside the displayed draft.
"""

from __future__ import annotations

import logging

from agents.base import build_agent
from intents import taxonomy
from models import ReviewResult

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the Reviewer & Editor for HESTA member communications.
Review a draft reply (written by the Writer) before a human sends it.

Check:
- accuracy_ok: the draft is consistent with the member's request and does not state facts not given.
- tone_ok: warm, plain-English, supportive HESTA house style.
- compliance_ok: it does NOT promise/confirm a regulated outcome (approval, eligibility, amount, timing);
  it does NOT contain personal financial/investment/product advice or a recommendation for the member's
  situation (which HESTA must not give — general information and referral to advice services is fine);
  if identity is not verified, it asks for verification rather than actioning the request; next steps
  are appropriate for the intent.
- If the draft contains personal advice, set compliance_ok=false and approved_for_human_send=false, and
  note it in issues.
- approved_for_human_send: true only if all three checks pass.
- edits: concise suggested wording changes (or "" if none).
- issues: specific problems (or empty).

Be strict on compliance for regulated intents.
"""

_agent = None


def _get():
    global _agent
    if _agent is None:
        _agent = build_agent(_SYSTEM_PROMPT, fast=False)
    return _agent


async def review(draft, intent_result, profile) -> ReviewResult:
    regulated = taxonomy.is_regulated(intent_result.primary_intent_id)
    prompt = (
        f"Intent: {draft.intent_id} ({taxonomy.name_for(draft.intent_id)}); regulated: {regulated}\n"
        f"Verification state: {draft.verification_state}\n\n"
        f"DRAFT SUBJECT: {draft.subject}\n\n"
        f"DRAFT BODY:\n{draft.body}"
    )
    try:
        return await _get().structured_output_async(ReviewResult, prompt)
    except Exception as exc:  # noqa: BLE001 — fail safe: not approved, needs a human
        log.warning("Reviewer failed; marking not-approved for human send: %s", exc)
        return ReviewResult(
            approved_for_human_send=False,
            accuracy_ok=False,
            tone_ok=False,
            compliance_ok=False,
            edits="",
            issues=["Automated review unavailable — a human must review the draft before sending."],
        )
