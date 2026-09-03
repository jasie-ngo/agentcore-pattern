"""AI-002 — Conversation Context Manager.

Reconstructs the (possibly threaded) email into a concise operational summary,
then pulls member identity via member_lookup and cases via case_lookup_creation.
"""

from __future__ import annotations

import logging

from agents.base import build_agent
from models import CaseSummary, IdentityInfo, CaseInfo
from tools import gateway

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
    if session_manager is not None:
        return build_agent(_SYSTEM_PROMPT, fast=False, session_manager=session_manager)
    global _agent
    if _agent is None:
        _agent = build_agent(_SYSTEM_PROMPT, fast=False)
    return _agent


async def summarize(inbound, mcp=None, session_manager=None) -> CaseSummary:
    """Summarize context and pull member identity + cases.

    Args:
        inbound: normalized email
        mcp: MCP Gateway client for member_lookup and case_lookup_creation
        session_manager: optional AgentCore Memory session
    """
    prompt = f"Channel: {inbound.channel}\nSender type: {inbound.sender_type}\n\nEMAIL:\n{inbound.latest_message}"
    try:
        result = await _get(session_manager).structured_output_async(CaseSummary, prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("Context Manager summary failed: %s", exc)
        text = (inbound.latest_message or "").strip()
        result = CaseSummary(
            summary=(text[:280] + "…") if len(text) > 280 else (text or "No readable message content."),
            conversation_state="new_request",
            outstanding_items=[],
        )

    # Now pull member identity and cases
    result.identity = await _lookup_member(mcp, inbound)
    result.cases = await _lookup_cases(mcp, result.identity)
    return result


async def _lookup_member(mcp, inbound) -> IdentityInfo:
    """Call member_lookup via MCP Gateway."""
    if mcp is None:
        return IdentityInfo(error="Gateway unavailable")

    # Try lookup by member ID first, then by email
    member_id = inbound.member_number_for_lookup
    email = inbound.from_email

    if member_id:
        result = await gateway.call_tool(mcp, "member_lookup", {"member_id": member_id})
    elif email:
        result = await gateway.call_tool(mcp, "member_lookup", {"email": email})
    else:
        return IdentityInfo(error="No member_id or email available")

    if "_gateway_error" in result:
        return IdentityInfo(error=f"Gateway error: {result['_gateway_error']}")
    if isinstance(result, dict) and result.get("error"):
        return IdentityInfo(error=result["error"])

    return IdentityInfo(
        member_id=result.get("member_id"),
        email=result.get("email"),
        name=result.get("name"),
        status=result.get("status"),
    )


async def _lookup_cases(mcp, identity: IdentityInfo) -> CaseInfo:
    """Call case_lookup_creation via MCP Gateway."""
    if mcp is None:
        return CaseInfo(error="Gateway unavailable", status="unavailable")
    if identity.error or not identity.member_id:
        return CaseInfo(error="Cannot lookup cases without valid member_id", status="unavailable")

    result = await gateway.call_tool(mcp, "case_lookup_creation", {"member_id": identity.member_id})

    if "_gateway_error" in result:
        return CaseInfo(error=f"Gateway error: {result['_gateway_error']}", status="error")
    if isinstance(result, dict) and result.get("error"):
        return CaseInfo(error=result["error"], status="error")

    status = result.get("status", "unknown")
    cases = result.get("cases", []) if status == "existing_cases_found" else []
    new_case = result.get("case") if status == "new_case_created" else None

    return CaseInfo(status=status, cases=cases, new_case=new_case)
