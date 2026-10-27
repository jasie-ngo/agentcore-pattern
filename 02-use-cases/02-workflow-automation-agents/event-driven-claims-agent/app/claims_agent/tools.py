"""Deterministic tools for the claims pipeline — referenced from ``config.yaml``.

``execute_claim_decision`` is one ``@tool`` that does the REJECT / AUTO_APPROVE /
HUMAN_REVIEW branching (``routing.py``) and calls the Gateway tools directly via
MCP, so execution costs zero extra LLM calls: the coordinator calls it once with
the Processor's and Validator's structured outputs and reports what it returns.

All Gateway calls are non-fatal — a failure is logged, reported in the summary,
and the remaining steps still run. ``create_claim`` derives the claim id from
policy number + source, so a re-delivered submission finds its existing claim;
when that happens the follow-up review request and notification are skipped.
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

from _shared import gateway
from _shared.gateway import GATEWAY_TOOL_NAMES, build_gateway_client
from strands import tool
from strands.tools.mcp import MCPClient

from claims_agent.routing import (
    AUTO_APPROVE,
    REJECT,
    decide_action,
    resolve_decision,
    resolve_routing,
)

log = logging.getLogger(__name__)


async def _call_gateway_tool(mcp: MCPClient, tool_name: str, arguments: dict) -> dict:
    """Call one Gateway tool directly via MCP — no LLM involved in picking it.

    Raises:
        RuntimeError: The Gateway (or a Cedar policy) reported an error.
    """
    result = await mcp.call_tool_async(
        tool_use_id=f"phase3-{uuid.uuid4().hex[:8]}",
        name=GATEWAY_TOOL_NAMES.get(tool_name, tool_name),
        arguments=arguments,
    )
    text = next((block["text"] for block in result.get("content", []) if "text" in block), "")
    if result.get("status") == "error":
        raise RuntimeError(text or "tool returned an error")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"raw": text}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


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
    source: str = "",
) -> str:
    """Execute the final claim action deterministically — no model call inside.

    Call this exactly ONCE, after the Processor's decision and the Validator's
    confidence/routing are both known. This tool decides REJECT / AUTO_APPROVE /
    HUMAN_REVIEW itself (REJECT always wins; AUTO_APPROVE below the confidence
    threshold is overridden to HUMAN_REVIEW) and calls the Gateway tools
    (create_claim, request_human_review, send_notification) directly. Report
    exactly what this tool says it did — do not re-derive or second-guess it.

    Args:
        decision: The Processor's ACCEPT or REJECT ("" if it gave none).
        policy_number: Policy number being claimed against.
        amount: Claim amount in dollars.
        category: Claim category.
        description: Brief description of the claim.
        reasoning: Why the Processor accepted or rejected.
        confidence: The Validator's 0-100 confidence score (0 if it gave none).
        routing: The Validator's AUTO_APPROVE or HUMAN_REVIEW ("" if it gave none).
        concerns: Validator's flagged concerns, or "None".
        claimant_email: Claimant's email for notification, or "" to skip.
        source: Where the submission came from (the envelope's ``source``), or "".
            Together with the policy number it identifies the claim, so a
            re-delivered submission is not recorded twice.

    Returns:
        A human-readable summary of what was executed.
    """
    final_decision = resolve_decision({"decision": decision} if decision else None, reasoning)
    confidence, final_routing = resolve_routing(
        {"confidence": confidence, "routing": routing} if routing else None, concerns
    )
    action = decide_action(final_decision, final_routing)
    lines = [f"Action: {action} (confidence: {confidence}/100)"]

    mcp = build_gateway_client()
    if mcp is None:
        lines.append(f"Gateway unavailable — no tool calls made ({gateway.LAST_ERROR}).")
        return "\n".join(lines)

    async def _safe_call(tool_name: str, arguments: dict, on_success) -> dict | None:
        try:
            result = await _call_gateway_tool(mcp, tool_name, arguments)
        except Exception as exc:  # noqa: BLE001 — Phase 3 actions are non-fatal
            log.warning("Gateway call %s failed (non-fatal): %s", tool_name, exc)
            lines.append(f"Could not call {tool_name}: {exc}")
            return None
        lines.append(on_success(result) if callable(on_success) else on_success)
        return result

    claim_fields = {
        "policy_number": policy_number,
        "description": description,
        "estimated_amount": amount,
        "category": category,
    }
    if source:
        claim_fields["source"] = source
    notify = bool(claimant_email) and claimant_email != "unknown"

    def _claim_line(prefix: str):
        def _line(result: dict) -> str:
            claim_id = result.get("claim_id", "unknown")
            if result.get("result") == "existing":
                return f"Claim already recorded (duplicate delivery): {claim_id} — no further actions"
            return f"{prefix}: {claim_id}"

        return _line

    def _is_duplicate(result: dict | None) -> bool:
        return bool(result) and result.get("result") == "existing"

    with mcp:
        if action == REJECT:
            if notify:
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
                {**claim_fields, "status": "approved", "decision": "auto_approved"},
                _claim_line("Claim created"),
            )
            if notify and not _is_duplicate(claim_result):
                await _safe_call(
                    "send_notification",
                    {
                        "recipient_email": claimant_email,
                        "subject": f"Claim Approved — Policy {policy_number}",
                        "body": (
                            f"Your claim has been approved.\n\n"
                            f"Amount: ${amount:,}\n"
                            f"Category: {category}\n\n"
                            f"You will receive further instructions shortly."
                        ),
                    },
                    f"Approval notification sent to {claimant_email}",
                )

        else:  # HUMAN_REVIEW
            claim_result = await _safe_call(
                "create_claim",
                {**claim_fields, "status": "pending_review", "decision": "escalated"},
                _claim_line("Claim created (pending review)"),
            )
            claim_id = claim_result.get("claim_id", "unknown") if claim_result else None
            duplicate = _is_duplicate(claim_result)

            if claim_id and not duplicate:
                await _safe_call(
                    "request_human_review",
                    {
                        "claim_id": claim_id,
                        "reason": f"Confidence: {confidence}/100. {concerns}",
                        "estimated_amount": amount,
                    },
                    "Escalated to human review",
                )

            if notify and not duplicate:
                await _safe_call(
                    "send_notification",
                    {
                        "recipient_email": claimant_email,
                        "subject": f"Claim Under Review — Policy {policy_number}",
                        "body": (
                            f"Your claim has been received and is currently under review "
                            f"by our claims team.\n\n"
                            f"Claim ID: {claim_id or 'pending'}\n"
                            f"Amount: ${amount:,}\n\n"
                            f"You will be contacted once a decision is made."
                        ),
                    },
                    f"Review notification sent to {claimant_email}",
                )

    return "\n".join(lines)
