"""Structured-output contracts for the HESTA pilot pipeline.

Each analysis agent (AI-001/002/005/011/012) returns one of these validated
Pydantic objects via ``Agent.structured_output_async``. The deterministic steps
(AI-003 identity, AI-004 attachments, routing) also return these types so the
orchestrator in ``main.py`` works with one consistent shape throughout.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ─── AI-001 Intent Identifier ────────────────────────────────────────────────


class DetectedIntent(BaseModel):
    """A single intent the member's email expresses."""

    intent_id: str = Field(description="A taxonomy intent id (see intents/taxonomy.py) or 'other_unknown'.")
    confidence: int = Field(ge=0, le=100, description="Confidence this intent is present, 0-100.")
    rationale: str = Field(description="Why this intent was chosen.")
    evidence_quote: str = Field(default="", description="A short verbatim span from the email supporting it.")


class IntentResult(BaseModel):
    """AI-001 output: one or more intents, ranked, plus sender classification."""

    intents: list[DetectedIntent] = Field(description="All intents found, highest confidence first.")
    primary_intent_id: str = Field(description="The single most likely intent id.")
    sender_type: str = Field(description="member | non_member | solicitor | unknown")
    needs_human_triage: bool = Field(
        default=False, description="True if ambiguous, conflicting, or nothing matches confidently."
    )
    personal_advice_requested: bool = Field(
        default=False,
        description=(
            "True if the sender is asking for PERSONAL financial/investment/product advice or a "
            "recommendation for their situation (e.g. 'which option is best for me?', 'should I switch/"
            "roll over?', 'what should I invest in?'). HESTA must not provide personal advice."
        ),
    )


# ─── AI-002 Conversation Context Manager ─────────────────────────────────────


class CaseSummary(BaseModel):
    """AI-002 output: an operational summary of the (possibly threaded) email."""

    summary: str = Field(description="Concise operational summary of the case for a HESTA agent.")
    conversation_state: str = Field(
        description="e.g. new_request | awaiting_identity_verification | chasing_update | providing_info"
    )
    outstanding_items: list[str] = Field(default_factory=list, description="What is still needed to progress.")


# ─── AI-003 Identity & Profiling (deterministic — reuses lookup_policy) ───────


class MemberProfile(BaseModel):
    """AI-003 output: verification derived from the existing DynamoDB record."""

    member_number: str | None = Field(default=None, description="Resolved member/policy number, if any.")
    matched: bool = Field(default=False, description="A DynamoDB record was found for the number.")
    match_key: str | None = Field(default=None, description="How the record was found: member_number | none.")
    factors_matched: list[str] = Field(
        default_factory=list, description="Which identifiers matched: member_number, email, account_type, status."
    )
    account_type: str | None = Field(default=None, description="Account/policy type from the record.")
    member_status: str | None = Field(default=None, description="active | expired | closed …")
    verification_level: str = Field(default="unverified", description="verified | partial | unverified")
    verification_required: bool = Field(default=True, description="True until identity is sufficiently verified.")
    notes: str = Field(default="", description="Human-readable explanation of the verification outcome.")


# ─── AI-004 Attachment Validation (deterministic — metadata only in pilot) ────


class AttachmentAssessment(BaseModel):
    """AI-004 output: attachment expectation vs presence (no file bytes in the pilot)."""

    attachments_present: int = Field(default=0, description="Count of attachment markers detected.")
    expected_document: str = Field(default="none", description="Document expected for the primary intent, or 'none'.")
    status: str = Field(default="not_applicable", description="ok | missing | present_unverified | not_applicable")
    notes: str = Field(default="", description="Explanation for a HESTA agent.")


# ─── Case status (AI-002 companion — reuses list_pending_claims) ──────────────


class CaseStatusResult(BaseModel):
    """Existing pending cases for the member, from the list_pending_claims Gateway tool.

    Only populated for status/progress enquiries; used to give the Writer real case
    context ("your case CLM-… is pending review") instead of a generic reply.
    """

    checked: bool = Field(default=False, description="True if we actually queried pending cases.")
    member_pending: list[dict] = Field(
        default_factory=list, description="Pending cases whose policy/member number matches this member."
    )
    total_pending: int = Field(default=0, description="Total pending cases returned (all members).")
    note: str = Field(default="", description="Human-readable summary of what was found.")


# ─── AI-005 Empathy ───────────────────────────────────────────────────────────


class EmpathyAssessment(BaseModel):
    """AI-005 output: emotional/vulnerability signals and operational priority."""

    sentiment: str = Field(description="positive | neutral | negative")
    vulnerability_flags: list[str] = Field(
        default_factory=list,
        description="e.g. financial_distress, bereavement, accessibility_need, legal_urgency, elderly",
    )
    complaint_indicator: bool = Field(default=False, description="True if the email reads as a complaint.")
    priority: str = Field(description="low | normal | high | urgent")
    recommended_attention: str = Field(description="One line on how the human agent should approach this member.")


# ─── AI-011 Writer / AI-012 Reviewer ─────────────────────────────────────────


class DraftEmail(BaseModel):
    """AI-011 output. In the pilot this is DISPLAYED as agent output — never auto-sent."""

    subject: str = Field(description="Reply subject line.")
    body: str = Field(description="Full HESTA-voice reply for a staff member to review and send.")
    intent_id: str = Field(description="The intent this reply addresses.")
    verification_state: str = Field(description="verified | needs_verification — drives what the draft asks for.")
    kb_snippets_used: list[str] = Field(default_factory=list, description="Which inline HESTA snippets informed it.")
    assumptions: list[str] = Field(default_factory=list, description="Assumptions a human should confirm.")


class ReviewResult(BaseModel):
    """AI-012 output: a compliance/quality check of the draft before a human sends."""

    approved_for_human_send: bool = Field(description="True if the draft is safe for a human to send as-is.")
    accuracy_ok: bool = Field(description="Facts/claims are consistent with the request and HESTA knowledge.")
    tone_ok: bool = Field(description="Tone matches HESTA's supportive house style.")
    compliance_ok: bool = Field(description="No unverified regulated action, no promises, correct next steps.")
    edits: str = Field(default="", description="Suggested edits, or '' if none.")
    issues: list[str] = Field(default_factory=list, description="Specific problems found, or empty.")


# ─── Routing gate (deterministic) ─────────────────────────────────────────────


class RoutingDecision(BaseModel):
    """Reuses the spirit of today's routing: decide whether a human is needed."""

    escalate_to_human: bool = Field(description="True if a human must review before anything is sent.")
    reasons: list[str] = Field(default_factory=list, description="Why the case was escalated.")
    regulated: bool = Field(default=False, description="True if the primary intent is a regulated request.")
