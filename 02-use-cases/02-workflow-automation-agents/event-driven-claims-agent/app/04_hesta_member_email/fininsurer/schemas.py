"""Structured-output contracts for the illustrative finance-insurer pipeline.

Every specialist agent returns one of these validated Pydantic objects. The overlay names the class it wants
(``agent_kwargs.structured_output: IntentResult``) and ``fininsurer/factory.py`` resolves the name against this module. The
deterministic tools (``assess_attachments``, ``classify_verification``, ``route_decision``, ``filter_customer_cases``) return the
same shapes as dicts, so the whole pipeline speaks one vocabulary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ─── Intent identifier ───────────────────────────────────────────────────────


class DetectedIntent(BaseModel):
    """A single intent the email expresses."""

    intent_id: str = Field(description="A taxonomy intent id (see fininsurer/taxonomy.py) or 'other_unknown'.")
    confidence: int = Field(ge=0, le=100, description="Confidence this intent is present, 0-100.")
    rationale: str = Field(description="Why this intent was chosen.")
    evidence_quote: str = Field(default="", description="A short verbatim span from the email supporting it.")


class IntentResult(BaseModel):
    """Intent identifier output: one or more intents, ranked, plus sender classification and the cross-cutting flags."""

    intents: list[DetectedIntent] = Field(description="All intents found, highest confidence first.")
    primary_intent_id: str = Field(description="The single most likely intent id.")
    sender_type: str = Field(description="policyholder | third_party | unknown")
    needs_human_triage: bool = Field(
        default=False, description="True if ambiguous, conflicting, or nothing matches confidently."
    )
    hardship_indicated: bool = Field(
        default=False,
        description=(
            "True if the sender says or clearly implies financial difficulty (cannot afford premiums or repayments, lost income, "
            "behind on payments, asks for a payment arrangement or relief), WHATEVER the primary intent is. Always goes to a person."
        ),
    )
    personal_advice_requested: bool = Field(
        default=False,
        description=(
            "True if the sender asks for PERSONAL advice for their own situation (e.g. 'should I cancel my policy?', 'which cover is "
            "best for me?'). The insurer must not give personal advice by email."
        ),
    )
    instructs_cancellation: bool = Field(
        default=False,
        description=(
            "True if the sender INSTRUCTS or AUTHORISES cancelling, reducing or pausing their cover (e.g. 'please cancel my "
            "policy'). False for a question about how cancellation works."
        ),
    )
    contact_detail_change: bool = Field(
        default=False,
        description=(
            "True if the sender asks to CHANGE the email address on their record, or to restore or reset their online access "
            "(e.g. 'can't log in'). Always goes to a person."
        ),
    )


# ─── Conversation context ────────────────────────────────────────────────────


class CaseSummary(BaseModel):
    """Context manager output: an operational summary of the (possibly threaded) email."""

    summary: str = Field(description="Concise operational summary of the case for an insurer staff member.")
    conversation_state: str = Field(
        description="e.g. new_request | awaiting_identity_verification | chasing_update | providing_info | complaint"
    )
    outstanding_items: list[str] = Field(default_factory=list, description="What is still needed to progress.")


# ─── Identity (deterministic: reuses lookup_policy) ──────────────────────────


class PolicyholderProfile(BaseModel):
    """Identity profiler output: verification derived from the existing policy record."""

    policy_number: str | None = Field(default=None, description="Resolved policy number, if any.")
    matched: bool = Field(default=False, description="A policy record was found for the number.")
    match_key: str | None = Field(default=None, description="How the record was found: policy_number | none.")
    factors_matched: list[str] = Field(
        default_factory=list, description="Which identifiers matched: policy_number, email, policy_type, status_verifiable."
    )
    policy_type: str | None = Field(default=None, description="Policy type from the record.")
    policy_status: str | None = Field(default=None, description="active | in_arrears | lapsed | cancelled ...")
    verification_level: str = Field(default="unverified", description="verified | partial | unverified")
    verification_required: bool = Field(default=True, description="True until identity is sufficiently verified.")
    notes: str = Field(default="", description="Human-readable explanation of the verification outcome.")


# ─── Attachments (deterministic: markers only, no file bytes) ────────────────


class AttachmentAssessment(BaseModel):
    """Attachment expectation vs presence. Attachments arrive as markers only: no file bytes."""

    attachments_present: int = Field(default=0, description="Count of attachment markers detected.")
    expected_document: str = Field(default="none", description="Document expected for the primary intent, or 'none'.")
    status: str = Field(default="not_applicable", description="ok | missing | present_unverified | not_applicable")
    notes: str = Field(default="", description="Explanation for a staff member.")


# ─── Case status (reuses list_pending_claims) ────────────────────────────────


class CaseStatusResult(BaseModel):
    """Existing pending cases for the policyholder, from the list_pending_claims Gateway tool."""

    checked: bool = Field(default=False, description="True if we actually queried pending cases.")
    customer_pending: list[dict] = Field(
        default_factory=list, description="Pending cases whose policy number matches this policyholder."
    )
    total_pending: int = Field(default=0, description="Total pending cases returned (all policyholders).")
    note: str = Field(default="", description="Human-readable summary of what was found.")


# ─── Empathy ─────────────────────────────────────────────────────────────────


class EmpathyAssessment(BaseModel):
    """Emotional and vulnerability signals, and operational priority."""

    sentiment: str = Field(description="positive | neutral | negative")
    vulnerability_flags: list[str] = Field(
        default_factory=list,
        description=(
            "e.g. financial_distress, family_violence, bereavement, mental_health, ill_health, accessibility_need, "
            "language_support, elderly, legal_urgency"
        ),
    )
    complaint_indicator: bool = Field(default=False, description="True if the email reads as a complaint.")
    priority: str = Field(description="low | normal | high | urgent")
    recommended_attention: str = Field(description="One line on how the staff member should approach this person.")


# ─── Writer and reviewer ─────────────────────────────────────────────────────


class DraftEmail(BaseModel):
    """Writer output. DISPLAYED for a person to send: never auto-sent."""

    subject: str = Field(description="Reply subject line.")
    body: str = Field(description="Full reply for a staff member to review and send.")
    intent_id: str = Field(description="The intent this reply addresses.")
    verification_state: str = Field(description="verified | needs_verification: drives what the draft asks for.")
    kb_snippets_used: list[str] = Field(default_factory=list, description="Which inline snippets informed it.")
    assumptions: list[str] = Field(default_factory=list, description="Assumptions a person should confirm.")


class ReviewResult(BaseModel):
    """Reviewer output: a compliance and quality check of the draft before a person sends it."""

    approved_for_human_send: bool = Field(description="True if the draft is safe for a person to send as-is.")
    accuracy_ok: bool = Field(description="Facts are consistent with the request and the approved knowledge.")
    tone_ok: bool = Field(description="Tone is supportive and free of pressure or judgement.")
    compliance_ok: bool = Field(description="No promised outcome, no personal advice, correct next steps.")
    edits: str = Field(default="", description="Suggested edits, or '' if none.")
    issues: list[str] = Field(default_factory=list, description="Specific problems found, or empty.")


# ─── Routing gate (deterministic) ────────────────────────────────────────────


class RoutingDecision(BaseModel):
    """Whether a person must review this case before anything is sent."""

    escalate_to_human: bool = Field(description="True if a person must review before anything is sent.")
    reasons: list[str] = Field(default_factory=list, description="Why the case was escalated.")
    human_decision_intent: bool = Field(
        default=False, description="True if the primary intent is one only a person may decide (claims, relief, cancellation)."
    )
    hardship: bool = Field(default=False, description="True if financial hardship was indicated by the flag or the message text.")
    priority_floor: str = Field(default="normal", description="The lowest priority a person should give this case: normal | high.")
