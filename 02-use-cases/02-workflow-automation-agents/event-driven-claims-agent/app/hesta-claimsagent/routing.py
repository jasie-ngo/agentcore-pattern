"""Human-in-the-loop routing gate for the HESTA pilot.

Reuses the *spirit* of the original claims routing (a deterministic gate, no LLM),
but the decision is now "does this need a human before anything is sent?" rather than
approve/reject. In the pilot, escalation causes a record to be written to DynamoDB via
the MCP Gateway (the hand-off) — see main.py. The Writer's draft is always produced
and displayed regardless.

Escalate when ANY of:
  - the primary intent is regulated (BDBN, BP, DASP, FH, FLS, NOI),
  - the client is not verified,
  - intent confidence is low / intent is other_unknown / the identifier flagged triage,
  - more than one confident intent was detected,
  - the empathy agent flags vulnerability or high/urgent priority,
  - an expected attachment was not detected (attachment status is "missing").
"""

from __future__ import annotations

from config import INTENT_CONFIDENCE_THRESHOLD
from intents import taxonomy
from models import AttachmentAssessment, EmpathyAssessment, IntentResult, MemberProfile, RoutingDecision


def _primary_confidence(intent_result: IntentResult) -> int:
    for i in intent_result.intents:
        if i.intent_id == intent_result.primary_intent_id:
            return i.confidence
    return intent_result.intents[0].confidence if intent_result.intents else 0


def decide(
    intent_result: IntentResult,
    profile: MemberProfile,
    empathy: EmpathyAssessment,
    attach: AttachmentAssessment,
) -> RoutingDecision:
    reasons: list[str] = []
    primary = intent_result.primary_intent_id
    regulated = taxonomy.is_regulated(primary)

    if regulated:
        reasons.append(f"regulated intent ({taxonomy.name_for(primary)})")

    # Personal advice must never be handled autonomously — always route to a human,
    # who is instructed not to provide personal advice either.
    if getattr(intent_result, "personal_advice_requested", False):
        reasons.append("PERSONAL ADVICE requested — do NOT provide personal financial advice")

    if profile.verification_required:
        reasons.append(f"identity not verified ({profile.verification_level})")

    confidence = _primary_confidence(intent_result)
    if primary == taxonomy.OTHER_UNKNOWN or intent_result.needs_human_triage:
        reasons.append("intent unclear / flagged for triage")
    elif confidence < INTENT_CONFIDENCE_THRESHOLD:
        reasons.append(f"low intent confidence ({confidence} < {INTENT_CONFIDENCE_THRESHOLD})")

    confident_intents = [i for i in intent_result.intents if i.confidence >= INTENT_CONFIDENCE_THRESHOLD]
    if len(confident_intents) > 1:
        reasons.append(f"multiple intents detected ({len(confident_intents)})")

    if empathy.vulnerability_flags or empathy.priority in ("high", "urgent"):
        flags = ", ".join(empathy.vulnerability_flags) or empathy.priority
        reasons.append(f"vulnerability/priority ({flags})")

    # A required document was expected but not detected — a human must chase it up
    # rather than the case silently proceeding as if nothing were missing.
    if attach.status == "missing":
        reasons.append(f"missing expected attachment ({attach.expected_document})")

    return RoutingDecision(escalate_to_human=bool(reasons), reasons=reasons, regulated=regulated)
