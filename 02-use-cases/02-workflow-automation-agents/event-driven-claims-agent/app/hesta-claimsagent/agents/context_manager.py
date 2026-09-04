"""AI-002 — Conversation Context Manager.

Reconstructs the (possibly threaded) email into a concise operational summary so a
HESTA agent understands the case without reading the whole thread.
"""

from __future__ import annotations

import logging

from agents.base import build_agent
from fabric import registry
from models import CaseSummary

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the Conversation Context Manager for HESTA member servicing.
Summarise an inbound member email (which may include quoted history) into a short operational
brief for a HESTA agent.

Return:
- summary: 1-3 sentences on what the member wants and any relevant history.
- conversation_state: one of new_request | awaiting_identity_verification | chasing_update |
  providing_info | complaint | other.
- outstanding_items: concrete things still needed to progress (e.g. "identity verification",
  "resend document as PDF"), or an empty list.

Be factual and concise. Do not invent details that are not present.
"""

_agent = None


def _get(session_manager=None):
    # With AgentCore Memory: build a fresh per-invocation agent bound to this member's
    # session so it records the contact and recalls the member's prior contacts.
    # Without memory (session_manager is None): reuse the cached stateless singleton.
    spec = registry.spec_for("context_manager", default_fast=False)
    if session_manager is not None:
        return build_agent(_SYSTEM_PROMPT, fast=spec.fast, guarded=spec.guarded, session_manager=session_manager)
    global _agent
    if _agent is None:
        _agent = build_agent(_SYSTEM_PROMPT, fast=spec.fast, guarded=spec.guarded)
    return _agent


async def summarize(inbound, session_manager=None) -> CaseSummary:
    prompt = f"Channel: {inbound.channel}\nSender type: {inbound.sender_type}\n\nEMAIL:\n{inbound.latest_message}"
    try:
        return await _get(session_manager).structured_output_async(CaseSummary, prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("Context Manager failed; using minimal summary: %s", exc)
        text = (inbound.latest_message or "").strip()
        return CaseSummary(
            summary=(text[:280] + "…") if len(text) > 280 else (text or "No readable message content."),
            conversation_state="new_request",
            outstanding_items=[],
        )
