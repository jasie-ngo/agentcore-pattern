"""Deterministic tools for the claims pipeline — ported from app/claimsagent.

``execute_claim_decision`` is the spike for the Phase 3 risk flagged in
PLAN-multiusecaseagent.md: option (b) — one @tool that encapsulates the
REJECT/AUTO_APPROVE/HUMAN_REVIEW branching and calls the Gateway tools
directly, so the agent makes zero extra LLM calls for execution. This is a
straight port of the if/elif logic in app/claimsagent/main.py (lines 388-538)
and the matrix in app/claimsagent/routing.py.

Depends on app/_shared/gateway.py (build_gateway_client, GATEWAY_TOOL_NAMES),
which does not exist yet — this file assumes PLAN-multiusecaseagent.md step 1
has been done. Until then it will fail to import.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strands import tool

from _shared.gateway import GATEWAY_TOOL_NAMES, build_gateway_client

log = logging.getLogger(__name__)

REJECT = "REJECT"
AUTO_APPROVE = "AUTO_APPROVE"
HUMAN_REVIEW = "HUMAN_REVIEW"


def _decide_action(decision: str, routing: str) -> str:
    """Same matrix as claimsagent/routing.py:decide_action — REJECT always wins."""
    if decision.upper() == REJECT:
        return REJECT
    if routing.upper() == AUTO_APPROVE:
        return AUTO_APPROVE
    return HUMAN_REVIEW


async def _call_gateway_tool(mcp, tool_name: str, arguments: dict) -> dict:
    """Call one Gateway tool directly via MCP — no LLM involved in picking it."""
    resolved_name = GATEWAY_TOOL_NAMES.get(tool_name, tool_name)
    result = await mcp.call_tool_async(
        tool_use_id=f"phase3-{uuid.uuid4().hex[:8]}",
        name=resolved_name,
        arguments=arguments,
    )
    if result and getattr(result, "content", None):
        for block in result.content:
            if hasattr(block, "text"):
                try:
                    return json.loads(block.text)
                except (json.JSONDecodeError, TypeError):
                    return {"raw": block.text}
    return {"raw": str(result)}


@tool
async def execute_claim_decision(
    decision: str,
    policy_number: str,
    amount: int,
    category: str,
    description: str,
    reasoning: str,
    confidence: int,
    routing: str,
    concerns: str = "None",
    claimant_email: str = "",
) -> str:
    """Execute the final claim action deterministically — no model call inside.

    Call this exactly ONCE, after the Processor's decision and the Validator's
    confidence/routing are both known. This tool decides REJECT / AUTO_APPROVE /
    HUMAN_REVIEW itself (REJECT always wins over routing, matching
    claimsagent/routing.py) and calls the Gateway tools (create_claim,
    send_notification, request_human_review) directly. Report exactly what
    this tool says it did — do not re-derive or second-guess the action.

    Args:
        decision: The Processor's ACCEPT or REJECT.
        policy_number: Policy number being claimed against.
        amount: Claim amount in dollars.
        category: Claim category.
        description: Brief description of the claim.
        reasoning: Why the Processor accepted or rejected.
        confidence: The Validator's 0-100 confidence score.
        routing: The Validator's AUTO_APPROVE or HUMAN_REVIEW.
        concerns: Validator's flagged concerns, or "None".
        claimant_email: Claimant's email for notification, or "" to skip.

    Returns:
        A human-readable summary of what was executed.
    """
    action = _decide_action(decision, routing)
    lines = [f"Action: {action} (confidence: {confidence}/100)"]

    mcp = build_gateway_client()
    if mcp is None:
        lines.append("Gateway unavailable — no tool calls made.")
        return "\n".join(lines)

    async def _safe_call(tool_name: str, arguments: dict, on_success: str) -> dict | None:
        try:
            result = await _call_gateway_tool(mcp, tool_name, arguments)
            lines.append(on_success)
            return result
        except Exception as exc:  # noqa: BLE001 — non-fatal, matches claimsagent/main.py
            log.warning("Gateway call %s failed (non-fatal): %s", tool_name, exc)
            lines.append(f"Could not call {tool_name}: {exc}")
            return None

    if action == REJECT:
        if claimant_email:
            await _safe_call(
                "send_notification",
                {
                    "recipient_email": claimant_email,
                    "subject": f"Claim Update — Policy {policy_number}",
                    "body": f"Your claim has been reviewed and rejected.\n\nReason: {reasoning}",
                },
                f"Notification sent to {claimant_email}",
            )

    elif action == AUTO_APPROVE:
        claim_result = await _safe_call(
            "create_claim",
            {
                "policy_number": policy_number,
                "description": description,
                "estimated_amount": amount,
                "category": category,
                "status": "approved",
                "decision": "auto_approved",
            },
            "Claim created",
        )
        claim_id = (claim_result or {}).get("claim_id", "unknown")
        lines[-1] = f"Claim created: {claim_id}"

        if claimant_email:
            await _safe_call(
                "send_notification",
                {
                    "recipient_email": claimant_email,
                    "subject": f"Claim Approved — Policy {policy_number}",
                    "body": f"Your claim has been approved.\n\nAmount: ${amount:,}\nCategory: {category}",
                },
                f"Approval notification sent to {claimant_email}",
            )

    else:  # HUMAN_REVIEW
        claim_result = await _safe_call(
            "create_claim",
            {
                "policy_number": policy_number,
                "description": description,
                "estimated_amount": amount,
                "category": category,
                "status": "pending_review",
                "decision": "escalated",
            },
            "Claim created (pending review)",
        )
        claim_id = (claim_result or {}).get("claim_id", "unknown")
        lines[-1] = f"Claim created (pending review): {claim_id}"

        await _safe_call(
            "request_human_review",
            {
                "claim_id": claim_id,
                "reason": f"Confidence: {confidence}/100. {concerns}",
                "estimated_amount": amount,
            },
            "Escalated to human review",
        )

        if claimant_email:
            await _safe_call(
                "send_notification",
                {
                    "recipient_email": claimant_email,
                    "subject": f"Claim Under Review — Policy {policy_number}",
                    "body": f"Your claim is under review.\n\nClaim ID: {claim_id}\nAmount: ${amount:,}",
                },
                f"Review notification sent to {claimant_email}",
            )

    return "\n".join(lines)
