"""Human-in-the-loop hand-off — writes a case + review record to DynamoDB via the MCP
Gateway (create_claim + request_human_review). Extracted from main.py so the fabric
graph's ``hitl_record`` node (fabric/adapters.py) can call it without importing main.
"""

from __future__ import annotations

import json

from tools import gateway


async def write_hitl_record(mcp, inbound, intent_result, profile, decision, draft) -> str:
    """Reuse create_claim + request_human_review to persist the case for a human.

    Non-fatal: surfaces the real Gateway error if it fails.
    """
    description = (inbound.latest_message or "").strip()[:1000] or draft.subject
    claim = await gateway.call_tool(
        mcp,
        "create_claim",
        {
            "policy_number": profile.member_number or "UNKNOWN",
            "description": description,
            "estimated_amount": 0,
            "category": intent_result.primary_intent_id,
            "status": "pending_review",
            "decision": "escalated",
        },
    )
    if isinstance(claim, dict) and "_gateway_error" in claim:
        return f"⚠️ Could not write the case record (Gateway error): {claim['_gateway_error']}\n\n"
    if isinstance(claim, dict) and claim.get("error"):
        return f"⚠️ create_claim returned an error: {claim['error']}\n\n"

    claim_id = claim.get("claim_id") if isinstance(claim, dict) else None
    if not claim_id:
        return (
            "⚠️ create_claim did not return a claim_id (record not confirmed). "
            f"Raw response: {json.dumps(claim)[:600]}\n\n"
        )

    lines = [f"📋 Case record written to DynamoDB (Claims): `{claim_id}`"]

    review = await gateway.call_tool(
        mcp,
        "request_human_review",
        {
            "claim_id": claim_id,
            "reason": "; ".join(decision.reasons) or "Manual review required",
            "estimated_amount": 0,
        },
    )
    if isinstance(review, dict) and "_gateway_error" in review:
        lines.append(f"⚠️ Review record not written (Gateway error): {review['_gateway_error']}")
    elif isinstance(review, dict) and review.get("error"):
        lines.append(f"⚠️ request_human_review returned an error: {review['error']}")
    else:
        lines.append("🔍 Review record written to DynamoDB (Reviews) — case is queued for a human.")
    return "\n".join(lines) + "\n\n"
