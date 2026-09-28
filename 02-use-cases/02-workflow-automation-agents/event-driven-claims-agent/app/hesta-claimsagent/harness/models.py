"""Orchestration result model for the HESTA harness."""

from __future__ import annotations

from pydantic import BaseModel, Field
from models import (
    IntentResult,
    CaseSummary,
    MemberProfile,
    AttachmentAssessment,
    EmpathyAssessment,
    DraftEmail,
    ReviewResult,
    DisclosureResult,
    RoutingDecision,
)


class OrchestrationResult(BaseModel):
    """Complete result of the 4-phase orchestration pipeline.

    All fields are optional (any phase may fail gracefully). The entrypoint
    (main.py) uses this to format and stream the final output.
    """

    # UNDERSTAND phase
    intent_result: IntentResult | None = Field(
        default=None,
        description="AI-001 Intent Identifier result."
    )
    case_summary: CaseSummary | None = Field(
        default=None,
        description="AI-002 Context Manager result (identity + cases + summary)."
    )

    # DECIDE phase
    member_profile: MemberProfile | None = Field(
        default=None,
        description="AI-003 Identity verification result (converted from identity)."
    )
    attachment_result: AttachmentAssessment | None = Field(
        default=None,
        description="AI-004 Attachment validation result."
    )
    empathy_result: EmpathyAssessment | None = Field(
        default=None,
        description="AI-005 Empathy assessment result."
    )
    routing_decision: RoutingDecision | None = Field(
        default=None,
        description="Routing decision (escalate/reject/auto-send)."
    )

    # EXECUTE phase
    draft: DraftEmail | None = Field(
        default=None,
        description="AI-011 Writer draft."
    )
    review_result: ReviewResult | None = Field(
        default=None,
        description="AI-012 Reviewer result (from final revision)."
    )
    revision_count: int = Field(
        default=0,
        description="Number of Writer-Reviewer revisions performed."
    )
    disclosure_result: DisclosureResult | None = Field(
        default=None,
        description="Disclosure Check result (sensitive account details)."
    )

    # HITL (Human-in-the-loop) tracking
    hitl_record_id: str | None = Field(
        default=None,
        description="Record ID if HITL record was written to DynamoDB."
    )

    # Memory tracking
    memory_recorded: bool = Field(
        default=False,
        description="True if turns were recorded to AgentCore Memory."
    )
    memory_failed: bool = Field(
        default=False,
        description="True if Memory recording failed."
    )
    memory_error: str | None = Field(
        default=None,
        description="Error message if Memory setup/recording failed."
    )

    # Error tracking
    errors: list[str] = Field(
        default_factory=list,
        description="Non-fatal errors encountered during orchestration."
    )

    # Metadata
    personal_advice_path: bool = Field(
        default=False,
        description="True if this took the personal advice special path."
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Idempotency key for this invocation (for deduplication)."
    )
