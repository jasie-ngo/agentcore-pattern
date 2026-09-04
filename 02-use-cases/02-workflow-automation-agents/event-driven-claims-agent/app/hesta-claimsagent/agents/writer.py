"""AI-011 — Writer.

Drafts a HESTA-voice reply email. In the pilot the draft is DISPLAYED as the agent's
output (never auto-sent). It uses the inline per-intent snippets in
``knowledge/hesta_snippets.py`` as its "approved knowledge" (no Bedrock KB yet) and
adapts to the verification state (unverified → ask for identity details).
"""

from __future__ import annotations

import logging

import config
from agents.base import build_agent
from fabric import registry
from intents import taxonomy
from knowledge import hesta_snippets
from models import DraftEmail

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = f"""You are the Writer for HESTA member servicing. You draft a reply email for a
HESTA staff member to review and send. You are NOT sending anything.

{hesta_snippets.style_guide()}

Hard rules:
- Use ONLY the provided HESTA knowledge snippet and the member's message. Do not invent policy details,
  balances, dates, eligibility, amounts, or outcomes.
- If verification_state is "needs_verification", the draft's main action is to request the identity
  details (do not action the request itself). If "verified", write an intent-appropriate acknowledgement
  and next steps.
- Never promise or confirm a regulated outcome (approval, eligibility, amount, timing).
- NEVER provide personal financial, investment or product advice or recommendations (e.g. which option/
  product is best for the member, whether they should switch/roll over/contribute for their situation).
  If asked, decline and offer general information + how to get personal advice.
- Address the member by first name if one is available, otherwise a neutral greeting.
- Return subject, body, intent_id, verification_state, kb_snippets_used, and any assumptions a human
  should confirm.
"""

_IDENTITY_BLOCK = hesta_snippets.IDENTITY_VERIFICATION_REQUEST

_agent = None


def _get():
    # Guarded: the Bedrock Guardrail (no personal advice) is attached to the Writer model.
    global _agent
    if _agent is None:
        spec = registry.spec_for("writer", default_fast=False, default_guarded=True)
        _agent = build_agent(_SYSTEM_PROMPT, fast=spec.fast, guarded=spec.guarded)
    return _agent


def _advice_decline_draft(inbound, intent_result, profile) -> DraftEmail:
    """Deterministic, compliant reply when personal advice is requested — NO advice given.

    Used when AI-001 flags a personal-advice request; guarantees the draft never contains advice
    (belt-and-braces with the Bedrock Guardrail on the model).
    """
    body_lines = [hesta_snippets.GREETING, "", "Hi there,", "", hesta_snippets.PERSONAL_ADVICE_DECLINE]
    if profile.verification_required:
        body_lines += ["", _IDENTITY_BLOCK]
    body_lines += ["", hesta_snippets.SIGNOFF, "", hesta_snippets.LEGAL_FOOTER]
    return DraftEmail(
        subject="Re: your HESTA enquiry",
        body="\n".join(body_lines),
        intent_id=intent_result.primary_intent_id,
        verification_state="needs_verification" if profile.verification_required else "verified",
        kb_snippets_used=["personal_advice_decline"],
        assumptions=[
            "Personal advice was requested — this reply declines to advise and refers to HESTA advice "
            "services. A HESTA team member must handle this and must NOT provide personal advice."
        ],
    )


def _fallback_draft(inbound, intent_result, profile) -> DraftEmail:
    """Deterministic HESTA-voice draft used if the LLM call fails (keeps the pilot working)."""
    intent_id = intent_result.primary_intent_id
    needs_verify = profile.verification_required
    snippet = hesta_snippets.snippet_for(intent_id)
    body_lines = [hesta_snippets.GREETING, "", "Hi there,", "", snippet]
    if needs_verify:
        body_lines += ["", _IDENTITY_BLOCK]
    body_lines += ["", hesta_snippets.SIGNOFF, "", hesta_snippets.LEGAL_FOOTER]
    return DraftEmail(
        subject=f"HESTA — {taxonomy.name_for(intent_id)}",
        body="\n".join(body_lines),
        intent_id=intent_id,
        verification_state="needs_verification" if needs_verify else "verified",
        kb_snippets_used=[intent_id],
        assumptions=["Draft generated from template fallback (LLM unavailable) — review before sending."],
    )


def _status_context(status_ctx) -> str:
    """Render existing-case context for the prompt (only for status/progress enquiries)."""
    if status_ctx is None or not getattr(status_ctx, "checked", False):
        return ""
    if status_ctx.member_pending:
        cases = "; ".join(
            f"{c.get('claim_id', '?')} ({c.get('category', 'n/a')}, created {c.get('created_at', 'n/a')}, pending review)"
            for c in status_ctx.member_pending
        )
        return (
            "\nExisting pending case(s) for this member (from HESTA records): "
            f"{cases}\n"
            "The member is asking about status/progress — reference the relevant case id and that it is "
            "currently pending review. Do NOT invent a status, decision, date, or outcome beyond this.\n"
        )
    return (
        "\nNo pending case is on file for this member. If they believe they submitted something, "
        "acknowledge that we cannot locate it yet, and ask them to confirm how/when they submitted it "
        "(and to resend if appropriate). Do not confirm receipt of something we cannot find.\n"
    )


async def write(inbound, intent_result, profile, summary, empathy, status_ctx=None) -> DraftEmail:
    intent_id = intent_result.primary_intent_id
    verification_state = "needs_verification" if profile.verification_required else "verified"

    # Personal advice requested → deterministic compliant decline (no LLM advice risk).
    if getattr(intent_result, "personal_advice_requested", False):
        return _advice_decline_draft(inbound, intent_result, profile)

    prompt = (
        f"Primary intent: {intent_id} ({taxonomy.name_for(intent_id)})\n"
        f"Verification state: {verification_state} ({profile.notes})\n"
        f"Sender type: {intent_result.sender_type}\n"
        f"Member sentiment/priority: {empathy.sentiment} / {empathy.priority}; "
        f"vulnerability: {', '.join(empathy.vulnerability_flags) or 'none'}\n"
        f"Case summary: {summary.summary}\n"
        f"Outstanding items: {', '.join(summary.outstanding_items) or 'none'}\n"
        f"{_status_context(status_ctx)}\n"
        f"HESTA knowledge snippet to base the reply on:\n{hesta_snippets.snippet_for(intent_id)}\n\n"
        f"If verification is needed, include this exact block:\n{_IDENTITY_BLOCK}\n\n"
        f"Member's message:\n{inbound.latest_message}\n\n"
        "Write the draft reply now."
    )
    try:
        draft = await _get().structured_output_async(DraftEmail, prompt)
        # Keep machine fields authoritative regardless of what the model set.
        draft.intent_id = intent_id
        draft.verification_state = verification_state
        # If the Bedrock Guardrail intervened, its sentinel appears in the output → decline safely.
        if config.GUARDRAIL_BLOCK_SENTINEL in (draft.body or "") or config.GUARDRAIL_BLOCK_SENTINEL in (draft.subject or ""):
            log.warning("Guardrail intervened on Writer output; returning compliant advice decline.")
            return _advice_decline_draft(inbound, intent_result, profile)
        return draft
    except Exception as exc:  # noqa: BLE001 — includes guardrail interventions that break structured output
        log.warning("Writer failed (or guardrail intervened); using safe fallback: %s", exc)
        if getattr(intent_result, "personal_advice_requested", False):
            return _advice_decline_draft(inbound, intent_result, profile)
        return _fallback_draft(inbound, intent_result, profile)
