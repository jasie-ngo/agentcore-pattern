"""Deterministic tools for the HESTA pipeline — referenced from ``config.yaml``.

The hand-written agent ran these as plain Python steps inside its orchestrator.
Here the orchestration lives in YAML, so each one becomes a ``@tool`` that the
agent which used to depend on it calls directly.  The logic is a straight port —
same rules, same thresholds, same wording — so a case routes the same way it did
before:

| Tool                          | Replaces in hesta-claimsagent            |
|-------------------------------|------------------------------------------|
| ``normalize_email``           | ``ingestion/email_normalizer.py``        |
| ``assess_attachments``        | ``agents/attachment_validation.py``      |
| ``classify_verification``     | ``agents/identity_profiling.py`` policy  |
| ``filter_member_cases``       | ``agents/case_status.py`` filter         |
| ``route_decision``            | ``routing.py``                           |
| ``hesta_knowledge``           | ``knowledge/hesta_snippets.py``          |
| ``identity_verification_block`` | the Writer's ``_IDENTITY_BLOCK``       |

Every tool returns JSON-serialisable data whose shape matches the corresponding
Pydantic contract in ``hesta/schemas.py``, so tool results and structured agent
outputs are interchangeable.

The Gateway tools (``lookup_policy``, ``list_pending_claims``, ``create_claim``,
``request_human_review``) are **not** here — they stay on the MCP Gateway and are
attached to the relevant agents as a live MCP tool provider (``hesta/gateway.py``).
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strands import tool

from hesta import knowledge, taxonomy
from hesta.ingestion import normalize_email as _normalize_email
from hesta.settings import INTENT_CONFIDENCE_THRESHOLD

# ─── Phase 0 — ingestion ──────────────────────────────────────────────────────


@tool
def normalize_email(raw: str, sender_email: str = "", source: str = "") -> dict:
    """Normalise a raw inbound email into the canonical envelope for the pipeline.

    Strips the security banner, the HESTA legal footer and quoted thread history
    to isolate the member's latest message; parses Contact-Us form fields; counts
    attachment markers; and extracts a real-looking member/policy number
    (placeholders like "[MEMBER NUMBER]" are treated as absent).

    Call this ONCE, first, on the raw email text. Every other step works from the
    fields it returns.

    Args:
        raw: The raw email text exactly as received.
        sender_email: Envelope sender address, if the caller knows it. Optional.
        source: Where the email came from (e.g. the S3 object key). Optional.

    Returns:
        channel, sender_type, from_email, subject, member_number,
        member_number_for_lookup, form_reason, latest_message, attachment_count.
    """
    inbound = _normalize_email(
        raw,
        sender_email=sender_email or None,
        source=source or None,
    )
    fields = asdict(inbound)
    fields.pop("raw", None)  # keep the tool result small; the agent already has it
    return fields


# ─── AI-004 — attachment validation ───────────────────────────────────────────


@tool
def assess_attachments(primary_intent_id: str, attachment_count: int) -> dict:
    """Compare the attachments an intent EXPECTS against what the email contained.

    Pilot scope: attachments arrive only as markers (no file bytes), so this
    checks expectation vs presence and flags missing or unexpected documents.
    Real document parsing (type, completeness, legibility) is post-pilot.

    Args:
        primary_intent_id: The primary intent id from the intent identifier.
        attachment_count: Attachment markers detected by ``normalize_email``.

    Returns:
        attachments_present, expected_document, status
        (ok | missing | present_unverified | not_applicable), and notes.
    """
    n = max(0, int(attachment_count))
    expected = taxonomy.expected_attachment(primary_intent_id)

    if expected == "none":
        if n == 0:
            status = "not_applicable"
            notes = "No attachment expected for this intent, and none detected."
        else:
            status = "present_unverified"
            notes = f"{n} attachment(s) detected; not expected for this intent — check relevance."
    else:
        if n == 0:
            status = "missing"
            notes = (
                f"Expected a {expected} for this request, but no attachment was detected "
                "— ask the member to provide it."
            )
        else:
            status = "present_unverified"
            notes = (
                f"{n} attachment(s) detected; expected a {expected}. "
                "Contents cannot be validated in the pilot (no file bytes) — a human should confirm."
            )

    return {
        "attachments_present": n,
        "expected_document": expected,
        "status": status,
        "notes": notes,
    }


# ─── AI-003 — identity verification policy ────────────────────────────────────


@tool
def classify_verification(
    member_number: str = "",
    sender_email: str = "",
    record_found: bool = False,
    record_email: str = "",
    record_status: str = "",
    record_policy_type: str = "",
    lookup_error: str = "",
    gateway_unavailable: bool = False,
) -> dict:
    """Apply HESTA's identity-verification policy to a policy-lookup result.

    Deterministic — call this AFTER ``lookup_policy`` on the MCP Gateway, passing
    the fields that came back. The pilot policy:

    * member number resolves a record AND the sender email matches AND the
      account is active  → **verified** (no human verification needed)
    * record found and active but the sender email does not match → **partial**
    * anything else (no number, no record, non-active status, lookup failed)
      → **unverified**

    Args:
        member_number: Member/policy number from ``normalize_email``. Empty if none.
        sender_email: The sender's address from ``normalize_email``.
        record_found: True if ``lookup_policy`` returned a record.
        record_email: The ``email`` field on that record.
        record_status: The ``status`` field on that record (e.g. "active").
        record_policy_type: The ``policy_type`` field on that record.
        lookup_error: The error text if the lookup failed or found nothing.
        gateway_unavailable: True when the Gateway itself could not be reached or
            authenticated (as opposed to the lookup running and finding no record).

    Returns:
        A MemberProfile: member_number, matched, match_key, factors_matched,
        account_type, member_status, verification_level, verification_required, notes.
    """
    number = (member_number or "").strip()

    if not number:
        return {
            "member_number": None,
            "matched": False,
            "match_key": None,
            "factors_matched": [],
            "account_type": None,
            "member_status": None,
            "verification_level": "unverified",
            "verification_required": True,
            "notes": "No member/policy number found in the email — identity cannot be verified.",
        }

    if lookup_error or not record_found:
        return {
            "member_number": number,
            "matched": False,
            # "none" means the lookup ran and found nothing; None means it never
            # ran (Gateway unreachable), so the number has not been tested yet.
            "match_key": None if gateway_unavailable else "none",
            "factors_matched": [],
            "account_type": None,
            "member_status": None,
            "verification_level": "unverified",
            "verification_required": True,
            "notes": lookup_error or f"No record found for {number}.",
        }

    factors = ["member_number"]  # the number resolved to a record
    email_match = bool(
        sender_email
        and record_email
        and sender_email.strip().lower() == record_email.strip().lower()
    )
    if email_match:
        factors.append("email")
    if record_policy_type:
        factors.append("account_type")
    if record_status == "active":
        factors.append("status_active")

    if email_match and record_status == "active":
        level, required = "verified", False
        notes = "Member number resolved and sender email matches an active record."
    elif record_status == "active":
        level, required = "partial", True
        notes = (
            "Record found and active, but sender email does not match "
            "— request identity verification."
        )
    else:
        level, required = "unverified", True
        notes = f"Record found but status is '{record_status}' — treat as unverified."

    return {
        "member_number": number,
        "matched": True,
        "match_key": "member_number",
        "factors_matched": factors,
        "account_type": record_policy_type or None,
        "member_status": record_status or None,
        "verification_level": level,
        "verification_required": required,
        "notes": notes,
    }


# ─── Case status — filter the member's own pending cases ──────────────────────


@tool
def filter_member_cases(claims: list, member_number: str = "") -> dict:
    """Filter ``list_pending_claims`` output down to this member's pending cases.

    ``list_pending_claims`` takes no arguments and returns ALL pending cases, so
    call this with its ``claims`` array to keep only the ones whose
    ``policy_number`` matches the member.

    Args:
        claims: The ``claims`` array returned by ``list_pending_claims``.
        member_number: The member/policy number from ``normalize_email``.

    Returns:
        A CaseStatusResult: checked, member_pending, total_pending, note.
    """
    number = (member_number or "").strip()
    items = [c for c in (claims or []) if isinstance(c, dict)]

    if not number:
        return {
            "checked": False,
            "member_pending": [],
            "total_pending": len(items),
            "note": "No member/policy number in the email — cannot match cases.",
        }

    mine = [c for c in items if str(c.get("policy_number", "")).strip() == number]
    note = (
        f"{len(mine)} pending case(s) on file for {number}."
        if mine
        else f"No pending case on file for {number} (of {len(items)} pending total)."
    )
    return {
        "checked": True,
        "member_pending": mine,
        "total_pending": len(items),
        "note": note,
    }


# ─── Routing gate — human in the loop ─────────────────────────────────────────


@tool
def route_decision(
    primary_intent_id: str,
    primary_confidence: int,
    confident_intent_count: int = 1,
    needs_human_triage: bool = False,
    personal_advice_requested: bool = False,
    verification_required: bool = True,
    verification_level: str = "unverified",
    vulnerability_flags: list | None = None,
    priority: str = "normal",
) -> dict:
    """Decide whether a human must review this case before anything is sent.

    Deterministic gate — no LLM judgement. Call it once, after identity and
    empathy are known. Escalates when ANY of:

    * the primary intent is regulated (BDBN, BP, DASP, FH, FLS, NOI),
    * personal financial advice was requested,
    * identity is not verified,
    * the intent is unclear / flagged for triage / below the confidence threshold,
    * more than one confident intent was detected,
    * the empathy assessment flags vulnerability or high/urgent priority.

    Args:
        primary_intent_id: Primary intent id from the intent identifier.
        primary_confidence: Confidence (0-100) of that primary intent.
        confident_intent_count: How many intents scored at or above the threshold.
        needs_human_triage: The intent identifier's triage flag.
        personal_advice_requested: The intent identifier's personal-advice flag.
        verification_required: From the identity profile.
        verification_level: verified | partial | unverified.
        vulnerability_flags: From the empathy assessment.
        priority: low | normal | high | urgent, from the empathy assessment.

    Returns:
        A RoutingDecision: escalate_to_human, reasons, regulated.
    """
    reasons: list[str] = []
    regulated = taxonomy.is_regulated(primary_intent_id)
    flags = [f for f in (vulnerability_flags or []) if f]

    if regulated:
        reasons.append(f"regulated intent ({taxonomy.name_for(primary_intent_id)})")

    # Personal advice must never be handled autonomously — always route to a
    # human, who is instructed not to provide personal advice either.
    if personal_advice_requested:
        reasons.append("PERSONAL ADVICE requested — do NOT provide personal financial advice")

    if verification_required:
        reasons.append(f"identity not verified ({verification_level})")

    if primary_intent_id == taxonomy.OTHER_UNKNOWN or needs_human_triage:
        reasons.append("intent unclear / flagged for triage")
    elif int(primary_confidence) < INTENT_CONFIDENCE_THRESHOLD:
        reasons.append(
            f"low intent confidence ({primary_confidence} < {INTENT_CONFIDENCE_THRESHOLD})"
        )

    if int(confident_intent_count) > 1:
        reasons.append(f"multiple intents detected ({confident_intent_count})")

    if flags or priority in ("high", "urgent"):
        reasons.append(f"vulnerability/priority ({', '.join(flags) or priority})")

    return {
        "escalate_to_human": bool(reasons),
        "reasons": reasons,
        "regulated": regulated,
    }


# ─── Writer knowledge — the pilot's "approved knowledge" ──────────────────────


@tool
def hesta_knowledge(intent_id: str) -> dict:
    """Return the approved HESTA reply guidance for an intent.

    This is the pilot's approved knowledge — a small inline set, not a Bedrock
    Knowledge Base. Base the draft ONLY on the snippet returned here plus the
    member's own message. Nothing here promises a regulated outcome.

    Args:
        intent_id: The primary intent id the reply addresses.

    Returns:
        intent_name, snippet (per-intent next steps), style_guide, greeting,
        signoff, legal_footer, and personal_advice_decline.
    """
    return {
        "intent_id": intent_id,
        "intent_name": taxonomy.name_for(intent_id),
        "snippet": knowledge.snippet_for(intent_id),
        "style_guide": knowledge.style_guide(),
        "greeting": knowledge.GREETING,
        "signoff": knowledge.SIGNOFF,
        "legal_footer": knowledge.LEGAL_FOOTER,
        "personal_advice_decline": knowledge.PERSONAL_ADVICE_DECLINE,
    }


@tool
def identity_verification_block() -> str:
    """Return the exact identity-verification wording HESTA uses.

    Include this block verbatim in the draft whenever verification_state is
    "needs_verification". Do not paraphrase it.
    """
    return knowledge.IDENTITY_VERIFICATION_REQUEST
