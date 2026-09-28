"""Harness utility functions."""

from __future__ import annotations

from typing import Any
from models import MemberProfile


def _disclosure_state(identity: dict) -> str:
    """Determine disclosure state based on identity lookup result.

    Distinguishes between system failure (lookup_failed) and genuine not-found.
    """
    if identity.get("lookup_failed"):
        return "lookup_failed"
    if not identity.get("member_id"):
        return "unverified"
    return "verified" if identity.get("status") == "active" else "partially_verified"


def convert_identity_to_profile(identity: dict | None, inbound: Any, intent_result: dict) -> dict:
    """Convert IdentityInfo from member_lookup into MemberProfile for routing.

    Args:
        identity: Identity info dict from member_lookup
        inbound: Normalized email object
        intent_result: Intent classification result

    Returns:
        MemberProfile dict
    """
    if not identity:
        identity = {}

    disclosure_state = _disclosure_state(identity)

    if identity.get("error"):
        return MemberProfile(
            member_number=getattr(inbound, "member_number_for_lookup", None),
            matched=False,
            verification_level="unverified",
            verification_required=True,
            disclosure_state=disclosure_state,
            notes=identity.get("error"),
        ).model_dump()

    return MemberProfile(
        member_number=identity.get("member_id"),
        matched=bool(identity.get("member_id")),
        match_key="member_id" if identity.get("member_id") else None,
        factors_matched=[k for k in ["member_id", "email"] if identity.get(k)],
        account_type=None,
        member_status=identity.get("status"),
        verification_level="verified" if identity.get("status") == "active" else "unverified",
        verification_required=False if identity.get("status") == "active" else True,
        disclosure_state=disclosure_state,
        notes=f"Member lookup found: {identity.get('member_id')}" if identity.get("member_id") else "No member found",
    ).model_dump()
