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


async def summarize(
    inbound, mcp=None, session_manager=None, *, primary_intent_id=None, idempotency_key=None, source_object_id=None
) -> CaseSummary:
    """Summarize context and pull member identity + cases.

    Args:
        inbound: normalized email
        mcp: MCP Gateway client for member_lookup and case_lookup_creation
        session_manager: optional AgentCore Memory session
    """
    identity = await _lookup_member(mcp, inbound)
    cases = await _lookup_cases(
        mcp, identity, inbound, intent_id=primary_intent_id,
        idempotency_key=idempotency_key, source_object_id=source_object_id
    )
    history = "\n\n".join(
        f"[{entry.get('timestamp', 'unknown')} | {entry.get('entry_type', 'unknown')}"
        f"{' | intent: ' + entry['intent_id'] if entry.get('intent_id') else ''}]\n"
        f"{entry.get('content', '')}"
        for entry in cases.conversation_history
    ) or "(none)"
    prompt = (
        f"Channel: {inbound.channel}\nSender type: {inbound.sender_type}\n\n"
        "BEGIN HISTORICAL CASE CONVERSATION (do not confuse with current email):\n"
        f"{history}\nEND HISTORICAL CASE CONVERSATION\n\n"
        f"CURRENT EMAIL:\n{inbound.latest_message}"
    )
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
    result.identity = identity
    result.cases = cases
    return result


async def _lookup_member(mcp, inbound) -> IdentityInfo:
    """Call member_lookup via MCP Gateway.

    Distinguishes a genuine "no match" (lookup ran fine, nothing found — disclosure_state
    "unverified") from a system failure (Gateway/DynamoDB error — disclosure_state
    "lookup_failed"). Both fail closed on disclosure, but only the latter is a system fault
    worth surfacing distinctly rather than treating as a normal not-found.
    """
    if mcp is None:
        return IdentityInfo(error="Gateway unavailable", lookup_failed=True)

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
        return IdentityInfo(error=f"Gateway error: {result['_gateway_error']}", lookup_failed=True)
    if isinstance(result, dict) and result.get("error"):
        return IdentityInfo(error=result["error"])

    return IdentityInfo(
        member_id=result.get("member_id"),
        email=result.get("email"),
        name=result.get("name"),
        status=result.get("status"),
    )


async def _lookup_cases(mcp, identity: IdentityInfo, inbound, *, intent_id=None, idempotency_key=None, source_object_id=None) -> CaseInfo:
    """Call case_lookup_creation via MCP Gateway."""
    if mcp is None:
        return CaseInfo(error="Gateway unavailable", status="unavailable")
    if not identity.member_id:
        return CaseInfo(error="Member identity was not established; case lookup skipped.", status="identity_unverified")
    case_input = {}
    case_input["member_id"] = identity.member_id
    if intent_id:
        case_input["primary_intent_id"] = intent_id
    if inbound.from_email:
        case_input["sender_email"] = inbound.from_email
    if inbound.latest_message:
        case_input["inbound_email"] = inbound.latest_message
    if idempotency_key:
        case_input["idempotency_key"] = idempotency_key
    if source_object_id:
        case_input["source_object_id"] = source_object_id
    case_input["pipeline_version"] = "hesta-v2"
    result = await gateway.call_tool(mcp, "case_lookup_creation", case_input)

    if "_gateway_error" in result:
        return CaseInfo(error=f"Gateway error: {result['_gateway_error']}", status="error")
    if isinstance(result, dict) and result.get("error"):
        return CaseInfo(error=result["error"], status="error")

    status = result.get("status", "unknown")
    cases = result.get("cases", []) if status == "existing_cases_found" else []
    new_case = result.get("case") if status == "new_case_created" else None

    return CaseInfo(
        status=status,
        cases=cases,
        new_case=new_case,
        case_id=result.get("case_id") or (new_case or {}).get("case_id") or (cases[0].get("case_id") if cases else None),
        conversation_history=result.get("conversation_history")
        or (new_case or {}).get("conversation_history")
        or (cases[0].get("conversation_history") if cases else [])
    )
