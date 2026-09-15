"""Case-status enrichment (AI-002 companion) — reuses the list_pending_claims MCP tool.

For status/progress enquiries (e.g. "has my withdrawal been processed?", "my transfer
isn't showing", "did you receive my form?"), this queries the member's existing pending
cases so the Writer can reference the real case (id + date + pending status) instead of a
vague reply. It only runs for those enquiries (gated by ``is_status_query``) to avoid an
extra Gateway call on every email.

Deterministic (no LLM). ``list_pending_claims`` takes no arguments and returns ALL pending
cases, so we filter to this member by policy/member number.
"""

from __future__ import annotations

import logging

from models import CaseStatusResult
from tools import gateway

log = logging.getLogger(__name__)

# Phrases that indicate the member is chasing status / progress / receipt.
_STATUS_KEYWORDS = (
    "received",
    "receive",
    "processed",
    "in progress",
    "progress",
    "status",
    "any update",
    "an update",
    "not showing",
    "hasn't shown",
    "haven't heard",
    "have not heard",
    "still waiting",
    "yet to",
    "how long",
    "when will",
    "following up",
    "follow up",
    "chase",
    "already sent",
    "did you get",
)


def is_status_query(inbound, summary, intent_result) -> bool:
    """True if the email is chasing the status of something already submitted."""
    if summary is not None and getattr(summary, "conversation_state", "") == "chasing_update":
        return True
    text = (inbound.latest_message or "").lower()
    return any(kw in text for kw in _STATUS_KEYWORDS)


async def lookup_pending(mcp, inbound) -> CaseStatusResult:
    """Return the member's pending cases (filtered from list_pending_claims)."""
    number = inbound.member_number_for_lookup

    if mcp is None:
        return CaseStatusResult(checked=False, note="Gateway unavailable — cannot check existing cases.")
    if not number:
        return CaseStatusResult(checked=False, note="No member/policy number in the email — cannot match cases.")

    result = await gateway.call_tool(mcp, "list_pending_claims", {})

    if isinstance(result, dict) and "_gateway_error" in result:
        return CaseStatusResult(checked=False, note=f"Could not list pending cases: {result['_gateway_error']}")
    if isinstance(result, dict) and result.get("error"):
        return CaseStatusResult(checked=False, note=f"list_pending_claims error: {result['error']}")

    claims = result.get("claims", []) if isinstance(result, dict) else []
    target = str(number).strip()
    mine = [c for c in claims if str(c.get("policy_number", "")).strip() == target]
    note = (
        f"{len(mine)} pending case(s) on file for {number}."
        if mine
        else f"No pending case on file for {number} (of {len(claims)} pending total)."
    )
    return CaseStatusResult(checked=True, member_pending=mine, total_pending=len(claims), note=note)
