"""Structured-output contracts for the claims pipeline.

Each agent returns one of these validated Pydantic objects
(``agent_kwargs.structured_output: claims_agent.schemas:ClaimDecision``), so the
coordinator receives machine-parseable JSON rather than prose.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClaimDecision(BaseModel):
    """Phase 1 — the Claims Processor's decision."""

    decision: str = Field(description="Exactly ACCEPT or REJECT")
    amount: int = Field(description="Claim amount in dollars as an integer")
    policy_number: str = Field(description="Policy number being claimed against")
    category: str = Field(
        description=(
            "Claim category (auto_collision, property_damage, theft, natural_disaster, medical)"
        )
    )
    description: str = Field(description="Brief description of the claim")
    reasoning: str = Field(description="Detailed explanation of why the claim was accepted or rejected")
    coverage_check: str = Field(
        description="Summary of coverage verification (limits, active status, deductible)"
    )


class ClaimValidation(BaseModel):
    """Phase 2 — the Validation Agent's independent review."""

    confidence: int = Field(ge=0, le=100, description="Score from 0-100 representing decision quality")
    routing: str = Field(description="Exactly AUTO_APPROVE or HUMAN_REVIEW")
    validation_notes: str = Field(description="Assessment of the processor's decision")
    concerns: str = Field(description='Any red flags or issues. "None" if clean')
