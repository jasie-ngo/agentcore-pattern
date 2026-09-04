"""AI-005 — Empathy Agent.

Detects sentiment, vulnerability indicators, complaint signals and operational
priority so a human agent knows how much care/attention the case needs. It does
NOT make operational decisions.
"""

from __future__ import annotations

import logging

from agents.base import build_agent
from fabric import registry
from models import EmpathyAssessment

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the Empathy Agent for HESTA member servicing.
Assess the member's email for emotional and vulnerability signals to guide how a human agent
should respond. You do NOT make any operational or eligibility decision.

Return:
- sentiment: positive | neutral | negative
- vulnerability_flags: any of financial_distress, bereavement, accessibility_need, legal_urgency,
  elderly, ill_health, hardship — include only those clearly indicated; else empty.
- complaint_indicator: true if the tone reads as a complaint or dissatisfaction.
- priority: low | normal | high | urgent (urgent for e.g. severe hardship, imminent court date).
- recommended_attention: one short sentence on how the agent should approach this member.

Be measured; do not over-escalate without clear signals.
"""

_agent = None


def _get():
    global _agent
    if _agent is None:
        spec = registry.spec_for("empathy", default_fast=True)
        _agent = build_agent(_SYSTEM_PROMPT, fast=spec.fast, guarded=spec.guarded)
    return _agent


async def assess(inbound) -> EmpathyAssessment:
    prompt = f"EMAIL:\n{inbound.latest_message}"
    try:
        return await _get().structured_output_async(EmpathyAssessment, prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("Empathy Agent failed; using neutral default: %s", exc)
        return EmpathyAssessment(
            sentiment="neutral",
            vulnerability_flags=[],
            complaint_indicator=False,
            priority="normal",
            recommended_attention="Respond courteously; no special signals detected.",
        )
