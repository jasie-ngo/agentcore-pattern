"""AI-003 — Identity & Profiling Agent.

REUSES the existing DynamoDB verification (per the plan's reuse decision): it calls
the current ``lookup_policy`` Gateway tool + PoliciesTable AS-IS — no new store, no
GSI, no schema change. It looks the record up by the member/policy number found in
the email, then derives a verification level by comparing the sender email and
account/policy type + status against that record.

This step is deterministic (no LLM). It uses the same MCP Gateway the original
claimsagent used; the Gateway call runs off the event loop (see tools/gateway.py).
If the Gateway is unreachable or no number is present, it degrades safely to
``unverified`` (verification_required=True) and surfaces the real reason in ``notes``.
"""

from __future__ import annotations

import logging

from models import MemberProfile
from tools import gateway

log = logging.getLogger(__name__)


async def profile(mcp, inbound, intent_result) -> MemberProfile:
    number = inbound.member_number_for_lookup

    if not number:
        return MemberProfile(
            member_number=None,
            matched=False,
            verification_level="unverified",
            verification_required=True,
            notes="No member/policy number found in the email — identity cannot be verified.",
        )

    result = await gateway.call_tool(mcp, "lookup_policy", {"policy_number": number})

    # Gateway itself could not be reached/authenticated — surface the real reason.
    if "_gateway_error" in result:
        return MemberProfile(
            member_number=number,
            matched=False,
            verification_level="unverified",
            verification_required=True,
            notes=f"Gateway lookup unavailable: {result['_gateway_error']}",
        )

    # The lookup_policy Lambda returns {"error": "Policy X not found"} when there's no record.
    if result.get("error"):
        return MemberProfile(
            member_number=number,
            matched=False,
            match_key="none",
            verification_level="unverified",
            verification_required=True,
            notes=str(result.get("error")),
        )

    factors = ["member_number"]  # the number resolved to a record
    record_email = str(result.get("email", "")).strip().lower()
    sender = str(inbound.from_email or "").strip().lower()
    email_match = bool(sender and record_email and sender == record_email)
    if email_match:
        factors.append("email")

    account_type = result.get("policy_type")
    if account_type:
        factors.append("account_type")
    status = result.get("status")
    if status == "active":
        factors.append("status_active")

    # Verification policy (pilot): number resolves a record AND sender email matches
    # AND the account is active -> verified. Otherwise partial/unverified.
    if email_match and status == "active":
        level, required = "verified", False
        notes = "Member number resolved and sender email matches an active record."
    elif status == "active":
        level, required = "partial", True
        notes = "Record found and active, but sender email does not match — request identity verification."
    else:
        level, required = "unverified", True
        notes = f"Record found but status is '{status}' — treat as unverified."

    return MemberProfile(
        member_number=number,
        matched=True,
        match_key="member_number",
        factors_matched=factors,
        account_type=account_type,
        member_status=status,
        verification_level=level,
        verification_required=required,
        notes=notes,
    )
