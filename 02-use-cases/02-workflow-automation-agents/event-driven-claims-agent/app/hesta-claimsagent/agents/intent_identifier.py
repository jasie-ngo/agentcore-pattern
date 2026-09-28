"""AI-001 — Intent Identifier.

Classifies why the member is contacting HESTA (one or more reasons), with a
confidence per intent and a sender-type classification. Attachment *detection* is
done deterministically in the normalizer; this agent focuses on intent.
"""

from __future__ import annotations

import logging

from agents.base import build_agent
from intents import taxonomy
from models import DetectedIntent, IntentResult

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = f"""You are the Intent Identifier for HESTA (an Australian industry super fund).
Given an inbound member email, determine WHY the member is contacting HESTA.

Choose from this fixed taxonomy of intents:

{taxonomy.render_for_prompt()}

Rules:
- A member may raise MORE THAN ONE intent — return every intent you find, each with its own
  confidence (0-100), ranked highest first.
- Set primary_intent_id to the single most likely intent.
- DASP vs withdrawal_benefit_payment: prefer departing_australia_payment when there are
  residency/departure/overseas signals; otherwise withdrawal_benefit_payment.
- sender_type: "member", "non_member" (e.g. a prospective member), "solicitor" (law firm /
  professional correspondence), or "unknown".
- If nothing matches confidently, use primary_intent_id "other_unknown" and set needs_human_triage true.
- Set needs_human_triage true when the request is ambiguous, conflicting, or you are unsure.
- Classify from meaning, not personal data. Placeholders like [MEMBER NUMBER] carry no intent.

PERSONAL ADVICE FLAG (important — HESTA must not give personal advice):
- Set personal_advice_requested = true when the sender asks for PERSONAL financial/investment/product
  advice or a recommendation for THEIR situation. Examples: "which option/product is best for me?",
  "should I switch to high growth?", "should I roll over / consolidate for my situation?", "what should
  I invest in?", "what should I do with my super?", "is X a good idea for me?".
- This is independent of the intent: e.g. a rollover enquiry that asks "is it a good idea for me to roll
  over?" is rollover_transfer_combine AND personal_advice_requested = true.
- Do NOT set it for requests for general information or process ("how do I roll over?", "what are the
  eligibility rules?") — those are not personal advice.
"""

_agent = None


def _get():
    global _agent
    if _agent is None:
        _agent = build_agent(_SYSTEM_PROMPT, fast=True)
    return _agent


async def identify(inbound) -> IntentResult:
    prompt = (
        "Classify the intent(s) of this inbound email.\n\n"
        f"Channel: {inbound.channel}\n"
        f"Contact-form reason (hint only, may be missing/coarse): {inbound.form_reason}\n"
        f"Attachments detected: {inbound.attachment_count}\n\n"
        f"EMAIL:\n{inbound.latest_message}"
    )
    try:
        result = await _get().structured_output_async(IntentResult, prompt)
        # Guard against an out-of-taxonomy primary intent.
        if result.primary_intent_id not in taxonomy.VALID_IDS:
            result.primary_intent_id = taxonomy.OTHER_UNKNOWN
            result.needs_human_triage = True
        return result
    except Exception as exc:  # noqa: BLE001 — degrade safely to human triage
        log.warning("Intent Identifier failed; defaulting to other_unknown: %s", exc)
        return IntentResult(
            intents=[
                DetectedIntent(
                    intent_id=taxonomy.OTHER_UNKNOWN,
                    confidence=0,
                    rationale="Intent classification failed; routing to human triage.",
                )
            ],
            primary_intent_id=taxonomy.OTHER_UNKNOWN,
            sender_type=inbound.sender_type or "unknown",
            needs_human_triage=True,
        )
