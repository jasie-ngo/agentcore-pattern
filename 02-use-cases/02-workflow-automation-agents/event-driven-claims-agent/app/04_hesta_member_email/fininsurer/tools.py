"""Deterministic tools for the illustrative finance-insurer pipeline: referenced from the overlay.

Each is a ``@tool`` that the agent which depends on it calls directly. Every one returns JSON-serialisable data whose shape matches
the contract in ``fininsurer/schemas.py``, so tool results and structured agent outputs are interchangeable.

| Tool                            | What it decides                                                                   |
|---------------------------------|-----------------------------------------------------------------------------------|
| ``normalize_email``             | the canonical envelope: latest message, policy number, attachments, sender kind   |
| ``assess_attachments``          | expected evidence vs the attachment markers present                               |
| ``classify_verification``       | how far the sender's identity is verified (and the ONLY way memory is opened)     |
| ``recall_customer_memory`` / ``remember_customer_contact`` | memory, for a verified policyholder only            |
| ``filter_customer_cases``       | the policyholder's own pending cases                                              |
| ``route_decision``              | whether a person must review before anything is sent                              |
| ``fininsurer_knowledge`` / ``identity_verification_block`` | the Writer's approved wording                  |

The Gateway tools (``lookup_policy``, ``list_pending_claims``, ``create_claim``, ``request_human_review``) are not here: they stay on
the MCP Gateway and are attached to the relevant agents as a live MCP tool provider.

ILLUSTRATIVE: the rules and thresholds are made up for a pre-sale demo, not a real insurer's.
"""

from __future__ import annotations

import re
import sys
from dataclasses import asdict
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strands import tool

from core import memory, profile
from fininsurer import knowledge, taxonomy
from fininsurer.ingestion import normalize_email as _normalize_email
from fininsurer.settings import INTENT_CONFIDENCE_THRESHOLD

# A policy in arrears or lapsed still belongs to its holder. These are the statuses under which a matching sender can be verified:
# a hardship customer is often behind on premiums, and locking them out of verification would fail exactly the people this
# pipeline exists to help. Any other status (cancelled, closed, unknown) is capped at partial and goes to a person.
VERIFIABLE_STATUSES = ("active", "in_arrears", "lapsed")

# ─── Ingestion ───────────────────────────────────────────────────────────────


@tool
def normalize_email(raw: str, sender_email: str = "", source: str = "", sender_authenticated: bool = False) -> dict:
    """Normalise a raw inbound email into the canonical envelope for the pipeline.

    Strips the security banner, the legal footer and quoted thread history to isolate the latest message; parses web-form fields;
    counts attachment markers; and extracts a real-looking policy number (placeholders like "[POLICY NUMBER]" are treated as absent).

    Call this ONCE, first, on the raw email text. Every other step works from the fields it returns.

    Args:
        raw: The raw email text exactly as received.
        sender_email: Envelope sender address, if the caller knows it. Optional.
        source: Where the email came from (e.g. the S3 object key). Optional.
        sender_authenticated: True only if the mail path reported a DMARC pass for the sender. Default false.

    Returns:
        channel, sender_type, from_email, subject, policy_number, policy_number_for_lookup, form_reason, latest_message,
        attachment_count, sender_authenticated.
    """
    inbound = _normalize_email(
        raw, sender_email=sender_email or None, source=source or None, sender_authenticated=sender_authenticated
    )
    fields = asdict(inbound)
    fields.pop("raw", None)  # keep the tool result small; the agent already has it
    return fields


# ─── Attachments ─────────────────────────────────────────────────────────────


@tool
def assess_attachments(primary_intent_id: str, attachment_count: int) -> dict:
    """Compare the attachments an intent EXPECTS against what the email contained.

    Attachments arrive only as markers (no file bytes), so this checks expectation vs presence and flags missing or unexpected
    documents. Reading the documents (type, completeness, legibility) is not done: a person confirms hardship evidence.

    Args:
        primary_intent_id: The primary intent id from the intent identifier.
        attachment_count: Attachment markers detected by ``normalize_email``.

    Returns:
        attachments_present, expected_document, status (ok | missing | present_unverified | not_applicable), and notes.
    """
    n = max(0, int(attachment_count))
    expected = taxonomy.expected_attachment(primary_intent_id)

    if expected == "none":
        if n == 0:
            status, notes = "not_applicable", "No attachment expected for this intent, and none detected."
        else:
            status, notes = "present_unverified", f"{n} attachment(s) detected; not expected for this intent: check relevance."
    elif n == 0:
        status = "missing"
        notes = f"Expected {expected}, but no attachment was detected. Do not make this a condition of help: invite them to send it."
    else:
        status = "present_unverified"
        notes = f"{n} attachment(s) detected; expected {expected}. Contents cannot be validated (no file bytes): a person must review."

    return {"attachments_present": n, "expected_document": expected, "status": status, "notes": notes}


# ─── Identity ────────────────────────────────────────────────────────────────


@tool
def classify_verification(
    policy_number: str = "",
    sender_email: str = "",
    record_found: bool = False,
    record_email: str = "",
    record_status: str = "",
    record_policy_type: str = "",
    lookup_error: str = "",
    gateway_unavailable: bool = False,
    sender_authenticated: bool = False,
) -> dict:
    """Apply the identity-verification policy to a policy-lookup result.

    Deterministic: call this AFTER ``lookup_policy`` on the MCP Gateway, passing the fields that came back. The policy:

    * the number resolves a record AND the sender email matches AND the status is active, in arrears or lapsed AND the sender is
      authenticated (a DMARC pass) -> **verified**. Under the pre-sale deployment profile only, the address match alone is accepted.
    * the same, but the sender is not authenticated (strict profile) -> **partial**
    * record found but the sender email does not match, or the status is not one that can be verified -> **partial**
    * anything else (no number, no record, lookup failed) -> **unverified**

    Arrears and lapsed do NOT fail verification: a person behind on premiums is who the hardship path is for.

    Args:
        policy_number: Policy number from ``normalize_email``. Empty if none.
        sender_email: The sender's address from ``normalize_email``.
        record_found: True if ``lookup_policy`` returned a record.
        record_email: The ``email`` field on that record.
        record_status: The ``status`` field on that record (e.g. "active").
        record_policy_type: The ``policy_type`` field on that record.
        lookup_error: The error text if the lookup failed or found nothing.
        gateway_unavailable: True when the Gateway itself could not be reached or authenticated (as opposed to the lookup
            running and finding no record).
        sender_authenticated: The ``sender_authenticated`` value from ``normalize_email``. Default false.

    Returns:
        A PolicyholderProfile: policy_number, matched, match_key, factors_matched, policy_type, policy_status,
        verification_level, verification_required, notes.
    """
    number = (policy_number or "").strip()

    if not number:
        return {
            "policy_number": None,
            "matched": False,
            "match_key": None,
            "factors_matched": [],
            "policy_type": None,
            "policy_status": None,
            "verification_level": "unverified",
            "verification_required": True,
            "notes": "No policy number found in the email: identity cannot be verified.",
        }

    if lookup_error or not record_found:
        return {
            "policy_number": number,
            "matched": False,
            # "none" means the lookup ran and found nothing; None means it never ran (Gateway unreachable).
            "match_key": None if gateway_unavailable else "none",
            "factors_matched": [],
            "policy_type": None,
            "policy_status": None,
            "verification_level": "unverified",
            "verification_required": True,
            "notes": lookup_error or f"No record found for {number}.",
        }

    status = (record_status or "").strip().lower()
    factors = ["policy_number"]
    email_match = bool(sender_email and record_email and sender_email.strip().lower() == record_email.strip().lower())
    if email_match:
        factors.append("email")
    if record_policy_type:
        factors.append("policy_type")
    if status in VERIFIABLE_STATUSES:
        factors.append("status_verifiable")

    if email_match and status in VERIFIABLE_STATUSES:
        if sender_authenticated or profile.is_presale():
            level, required = "verified", False
            notes = f"Policy number resolved and sender email matches a record with status '{status}'."
            if not sender_authenticated:
                notes += " Pre-sale profile: the address match is accepted without sender authentication."
            memory.register_verified(number, sender_email)  # the only way memory is ever opened for this policyholder
        else:
            level, required = "partial", True
            notes = (
                "Sender email matches the record, but the sender is not authenticated (no DMARC pass), so verification is capped "
                "at partial under the strict profile: request identity verification."
            )
    elif email_match:
        level, required = "partial", True
        notes = f"Record found and email matches, but status is '{record_status}': a person must confirm before anything else."
    else:
        level, required = "partial", True
        notes = "Record found, but the sender email does not match: request identity verification."

    return {
        "policy_number": number,
        "matched": True,
        "match_key": "policy_number",
        "factors_matched": factors,
        "policy_type": record_policy_type or None,
        "policy_status": record_status or None,
        "verification_level": level,
        "verification_required": required,
        "notes": notes,
    }


# ─── Memory: only for a verified policyholder ────────────────────────────────


@tool
def recall_customer_memory(policy_number: str, sender_email: str, latest_message: str) -> dict:
    """Recall this policyholder's earlier contacts from AgentCore Memory.

    Call this ONLY after ``identity_profiler`` returned verification_level "verified". The tool enforces that itself: unless
    ``classify_verification`` verified THIS policy number for THIS sender address, nothing is recalled and ``allowed`` is false.

    Args:
        policy_number: The policy number from ``normalize_email``.
        sender_email: The sender's address from ``normalize_email``.
        latest_message: The latest message, used to find relevant earlier contacts.

    Returns:
        allowed (bool), memories (list of earlier contacts, oldest facts first), reason.
    """
    return memory.recall(policy_number, sender_email, latest_message)


@tool
def remember_customer_contact(policy_number: str, sender_email: str, latest_message: str, summary: str) -> dict:
    """Record this contact in AgentCore Memory for the verified policyholder.

    Call this ONCE, at the end, and ONLY for a verified policyholder. Like ``recall_customer_memory`` it refuses anything else, so an
    unverified sender can never write into a policyholder's memory.

    Args:
        policy_number: The policy number from ``normalize_email``.
        sender_email: The sender's address from ``normalize_email``.
        latest_message: The latest message.
        summary: The context manager's summary of this contact.

    Returns:
        recorded (bool), reason.
    """
    return memory.remember(policy_number, sender_email, latest_message, summary)


# ─── Case status ─────────────────────────────────────────────────────────────


@tool
def filter_customer_cases(claims: list, policy_number: str = "") -> dict:
    """Filter ``list_pending_claims`` output down to this policyholder's pending cases.

    ``list_pending_claims`` takes no arguments and returns ALL pending cases, so call this with its ``claims`` array to keep only
    the ones whose ``policy_number`` matches.

    Args:
        claims: The ``claims`` array returned by ``list_pending_claims``.
        policy_number: The policy number from ``normalize_email``.

    Returns:
        A CaseStatusResult: checked, customer_pending, total_pending, note.
    """
    number = (policy_number or "").strip()
    items = [c for c in (claims or []) if isinstance(c, dict)]

    if not number:
        return {
            "checked": False,
            "customer_pending": [],
            "total_pending": len(items),
            "note": "No policy number in the email: cannot match cases.",
        }

    mine = [c for c in items if str(c.get("policy_number", "")).strip() == number]
    note = (
        f"{len(mine)} pending case(s) on file for {number}."
        if mine
        else f"No pending case on file for {number} (of {len(items)} pending total)."
    )
    return {"checked": True, "customer_pending": mine, "total_pending": len(items), "note": note}


# ─── Routing gate: human in the loop ─────────────────────────────────────────

# Deterministic backstops. The intent identifier sets the flags; these patterns catch the plain cases when it misses them. They
# ESCALATE on a hit and are deliberately broad: a false hit sends a case to a person, a miss is what the flags are for.
_APOS = "['’]"
_HARDSHIP = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bhardship\b",
        rf"\b(?:can{_APOS}?t|cannot|unable\s+to|struggling\s+to|not\s+able\s+to)\s+(?:afford|pay|keep\s+up|make)\b[^.\n?]{{0,50}}"
        r"\b(?:premiums?|payments?|instal?lments?|bills?|rent|mortgage|repayments?|excess)\b",
        r"\b(?:behind|in\s+arrears)\b[^.\n?]{0,40}\b(?:payments?|premiums?|repayments?|bills?|rent)\b",
        r"\b(?:lost|losing)\s+(?:my|our|his|her)\s+(?:job|income|work|hours)\b",
        r"\b(?:made\s+redundant|redundan(?:cy|t)|retrenched)\b",
        r"\bstruggling\s+(?:with|to)\s+(?:money|bills|payments?|pay|afford|make\s+ends\s+meet)\b",
        r"\bpayment\s+(?:plan|arrangement|pause|holiday|relief)\b",
    )
]
_CANCEL = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bplease\s+(?:cancel|stop|terminate)\b",
        r"\b(?:i|we)\s+(?:want|wish|would\s+like|need|am\s+going|are\s+going)\s+(?:to\s+)?cancel\b",
        r"\bcancel\s+(?:my|our|the)\s+(?:policy|policies|cover|insurance|plan|direct\s+debit)\b",
        rf"\b(?:i|we){_APOS}?(?:d|\s+would)?\s*(?:authori[sz]e|instruct)\b[^.\n?]{{0,60}}\b(?:cancel|terminate)\b",
    )
]
_CONTACT_OR_LOGIN = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:change|update|replace|amend)\b[^.\n?]{0,40}\be-?mail\b",
        r"\bnew\s+e-?mail\b",
        r"\b(?:reset|restore|recover|regain|unlock)\b[^.\n?]{0,40}\b(?:password|log\s?in|login|online|portal|access)\b",
        rf"\b(?:can{_APOS}?t|cannot|unable\s+to)\s+(?:log\s?in|login|access)\b",
        r"\bforgot(?:ten)?\b[^.\n?]{0,20}\b(?:password|login|log\s?in|username)\b",
        r"\blocked\s+out\b",
    )
]
# Vulnerability flags that raise the priority floor on their own.
_HIGH_PRIORITY_FLAGS = {"family_violence", "mental_health", "financial_distress", "bereavement", "legal_urgency"}


@tool
def route_decision(
    primary_intent_id: str,
    primary_confidence: int,
    confident_intent_count: int = 1,
    needs_human_triage: bool = False,
    hardship_indicated: bool = False,
    personal_advice_requested: bool = False,
    verification_required: bool = True,
    verification_level: str = "unverified",
    vulnerability_flags: list | None = None,
    priority: str = "normal",
    complaint_indicator: bool = False,
    instructs_cancellation: bool = False,
    contact_detail_change: bool = False,
    sender_type: str = "policyholder",
    latest_message: str = "",
) -> dict:
    """Decide whether a person must review this case before anything is sent.

    Deterministic gate: no LLM judgement. Call it once, after identity and empathy are known. Escalates when ANY of:

    * the primary intent is one only a person may decide (a claim, hardship relief, a cancellation, a complaint),
    * financial hardship is indicated (by the intent identifier's flag, the intent, or the message text),
    * personal advice was requested,
    * identity is not verified,
    * the sender is a third party (someone writing on a policyholder's behalf: authority must be confirmed),
    * the intent is unclear, flagged for triage, or below the confidence threshold,
    * more than one confident intent was detected,
    * the empathy assessment flags vulnerability, a complaint, or high/urgent priority,
    * the request is to cancel, reduce or pause cover, or to change the email on record or restore online access.

    Only an information or routine request from a verified policyholder, with none of the above, takes the light path.

    Args:
        primary_intent_id: Primary intent id from the intent identifier.
        primary_confidence: Confidence (0-100) of that primary intent.
        confident_intent_count: How many intents scored at or above the threshold.
        needs_human_triage: The intent identifier's triage flag.
        hardship_indicated: The intent identifier's hardship flag.
        personal_advice_requested: The intent identifier's personal-advice flag.
        verification_required: From the identity profile.
        verification_level: verified | partial | unverified.
        vulnerability_flags: From the empathy assessment.
        priority: low | normal | high | urgent, from the empathy assessment.
        complaint_indicator: From the empathy assessment.
        instructs_cancellation: From the intent identifier.
        contact_detail_change: From the intent identifier.
        sender_type: policyholder | third_party | unknown, from ``normalize_email``.
        latest_message: The latest message, for the backstop pattern checks.

    Returns:
        A RoutingDecision: escalate_to_human, reasons, human_decision_intent, hardship, priority_floor.
    """
    reasons: list[str] = []
    text = latest_message or ""
    flags = [f for f in (vulnerability_flags or []) if f]
    human_decision = taxonomy.is_human_decision(primary_intent_id)
    hardship = bool(hardship_indicated) or taxonomy.is_hardship(primary_intent_id) or any(p.search(text) for p in _HARDSHIP)

    if human_decision:
        reasons.append(f"only a person may decide this ({taxonomy.name_for(primary_intent_id)})")

    if hardship:
        reasons.append("FINANCIAL HARDSHIP indicated: a person assesses it; never promise or hint at an outcome")

    if personal_advice_requested:
        reasons.append("PERSONAL ADVICE requested: do NOT provide personal advice")

    if verification_required:
        reasons.append(f"identity not verified ({verification_level})")

    if sender_type == "third_party":
        reasons.append("sender is a third party: confirm their authority to act before sharing anything")

    if primary_intent_id == taxonomy.OTHER_UNKNOWN or needs_human_triage:
        reasons.append("intent unclear / flagged for triage")
    elif int(primary_confidence) < INTENT_CONFIDENCE_THRESHOLD:
        reasons.append(f"low intent confidence ({primary_confidence} < {INTENT_CONFIDENCE_THRESHOLD})")

    if int(confident_intent_count) > 1:
        reasons.append(f"multiple intents detected ({confident_intent_count})")

    if flags or priority in ("high", "urgent"):
        reasons.append(f"vulnerability/priority ({', '.join(flags) or priority})")

    if complaint_indicator or primary_intent_id == "complaint_dispute":
        reasons.append("complaint: route to the complaints process")

    if instructs_cancellation or any(p.search(text) for p in _CANCEL):
        reasons.append("request to cancel, reduce or pause cover")

    if contact_detail_change or any(p.search(text) for p in _CONTACT_OR_LOGIN):
        reasons.append("request to change the email on record or restore online access (account-takeover route)")

    high = hardship or priority in ("high", "urgent") or bool(_HIGH_PRIORITY_FLAGS & set(flags))
    return {
        "escalate_to_human": bool(reasons),
        "reasons": reasons,
        "human_decision_intent": human_decision,
        "hardship": hardship,
        "priority_floor": "high" if high else "normal",
    }


# ─── The Writer's approved wording ───────────────────────────────────────────


@tool
def fininsurer_knowledge(intent_id: str) -> dict:
    """Return the approved reply guidance for an intent.

    This is the pilot's approved knowledge: a small inline set, not a Bedrock Knowledge Base. Base the draft ONLY on the snippet
    returned here plus the person's own message. Nothing here promises an outcome.

    Args:
        intent_id: The primary intent id the reply addresses.

    Returns:
        intent_name, snippet (per-intent next steps), style_guide, greeting, signoff, legal_footer, personal_advice_decline.
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
    """Return the exact identity-verification wording to use.

    Include this block verbatim in the draft whenever verification_state is "needs_verification". Do not paraphrase it.
    """
    return knowledge.IDENTITY_VERIFICATION_REQUEST
