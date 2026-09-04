"""End-to-end: main.invoke() driven by the fabric executor produces the same markdown
sections, in the same order, as the pre-fabric pipeline (ADR-0015 decision 1). All AWS/
Bedrock/Gateway calls are mocked; normalize_email runs for real (pure text)."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from models import (  # noqa: E402
    AttachmentAssessment,
    CaseStatusResult,
    CaseSummary,
    DetectedIntent,
    DraftEmail,
    EmpathyAssessment,
    IntentResult,
    MemberProfile,
    ReviewResult,
    RoutingDecision,
)


async def _collect(agen):
    return [chunk async for chunk in agen]


class MainFabricPipelineTests(unittest.TestCase):
    def test_escalated_status_query_pipeline(self):
        import main

        intent_result = IntentResult(
            intents=[DetectedIntent(intent_id="withdrawal_benefit_payment", confidence=90, rationale="r")],
            primary_intent_id="withdrawal_benefit_payment",
            sender_type="member",
        )
        summary = CaseSummary(summary="Member is chasing a withdrawal.", conversation_state="chasing_update")
        status_ctx = CaseStatusResult(
            checked=True, member_pending=[{"claim_id": "CLM-1"}], total_pending=1, note="1 pending"
        )
        profile = MemberProfile(
            member_number="M123",
            matched=True,
            verification_level="unverified",
            verification_required=True,
            notes="partial",
        )
        attachments = AttachmentAssessment(
            attachments_present=0, expected_document="none", status="not_applicable", notes="n/a"
        )
        empathy = EmpathyAssessment(sentiment="neutral", priority="normal", recommended_attention="Be courteous.")
        decision = RoutingDecision(
            escalate_to_human=True, reasons=["identity not verified (unverified)"], regulated=False
        )
        draft = DraftEmail(
            subject="Re: withdrawal", body="Body text", intent_id="withdrawal_benefit_payment",
            verification_state="needs_verification",
        )
        review = ReviewResult(approved_for_human_send=True, accuracy_ok=True, tone_ok=True, compliance_ok=True)

        mcp = MagicMock()

        with patch("tools.gateway.get_mcp_client", return_value=mcp), patch(
            "memory.session.get_memory_session_manager", return_value=None
        ), patch("agents.intent_identifier.identify", new=AsyncMock(return_value=intent_result)), patch(
            "agents.context_manager.summarize", new=AsyncMock(return_value=summary)
        ), patch("agents.case_status.is_status_query", return_value=True), patch(
            "agents.case_status.lookup_pending", new=AsyncMock(return_value=status_ctx)
        ), patch("agents.identity_profiling.profile", new=AsyncMock(return_value=profile)), patch(
            "agents.attachment_validation.assess", return_value=attachments
        ), patch("agents.empathy.assess", new=AsyncMock(return_value=empathy)), patch(
            "routing.decide", return_value=decision
        ), patch("agents.writer.write", new=AsyncMock(return_value=draft)), patch(
            "agents.reviewer_editor.review", new=AsyncMock(return_value=review)
        ), patch(
            "hitl.write_hitl_record",
            new=AsyncMock(return_value="📋 Case record written to DynamoDB (Claims): `CLM-2`\n\n"),
        ):
            chunks = asyncio.run(
                _collect(
                    main.invoke(
                        {"prompt": "I haven't heard back about my withdrawal.", "claimant_email": "m@example.com"},
                        None,
                    )
                )
            )

        output = "".join(chunks)
        for marker in [
            "## 1 · Understand", "AI-001", "## 2 · Decide", "AI-003", "list_pending_claims",
            "## 3 · Execute", "Re: withdrawal", "AI-012", "Human-in-the-loop", "CLM-2",
            "## 4 · Learn", "Processing complete",
        ]:
            self.assertIn(marker, output)
        self.assertLess(output.index("## 1 · Understand"), output.index("## 2 · Decide"))
        self.assertLess(output.index("## 2 · Decide"), output.index("## 3 · Execute"))
        self.assertLess(output.index("## 3 · Execute"), output.index("## 4 · Learn"))

    def test_non_escalated_skips_hitl(self):
        import main

        intent_result = IntentResult(
            intents=[DetectedIntent(intent_id="general_enquiry", confidence=95, rationale="r")],
            primary_intent_id="general_enquiry",
            sender_type="member",
        )
        summary = CaseSummary(summary="General question.", conversation_state="new_request")
        profile = MemberProfile(
            member_number="M123", matched=True, verification_level="verified",
            verification_required=False, notes="ok",
        )
        attachments = AttachmentAssessment(
            attachments_present=0, expected_document="none", status="not_applicable", notes="n/a"
        )
        empathy = EmpathyAssessment(sentiment="neutral", priority="normal", recommended_attention="Be courteous.")
        decision = RoutingDecision(escalate_to_human=False, reasons=[], regulated=False)
        draft = DraftEmail(
            subject="Re: question", body="Body", intent_id="general_enquiry", verification_state="verified"
        )
        review = ReviewResult(approved_for_human_send=True, accuracy_ok=True, tone_ok=True, compliance_ok=True)

        mcp = MagicMock()

        with patch("tools.gateway.get_mcp_client", return_value=mcp), patch(
            "memory.session.get_memory_session_manager", return_value=None
        ), patch("agents.intent_identifier.identify", new=AsyncMock(return_value=intent_result)), patch(
            "agents.context_manager.summarize", new=AsyncMock(return_value=summary)
        ), patch("agents.case_status.is_status_query", return_value=False), patch(
            "agents.identity_profiling.profile", new=AsyncMock(return_value=profile)
        ), patch("agents.attachment_validation.assess", return_value=attachments), patch(
            "agents.empathy.assess", new=AsyncMock(return_value=empathy)
        ), patch("routing.decide", return_value=decision), patch(
            "agents.writer.write", new=AsyncMock(return_value=draft)
        ), patch("agents.reviewer_editor.review", new=AsyncMock(return_value=review)):
            chunks = asyncio.run(_collect(main.invoke({"prompt": "What are your hours?"}, None)))

        output = "".join(chunks)
        # No HITL section: the hitl_record node is gated off by the escalate_to_human router.
        # (The Routing section always carries a "**Human-in-the-loop:** no" line — that is the
        # unchanged pre-fabric wording, so assert on the section heading, not the bare phrase.)
        self.assertNotIn("### 👤 Human-in-the-loop", output)
        self.assertIn("- **Human-in-the-loop:** no", output)
        self.assertIn("Processing complete", output)

    def test_exception_in_graph_propagates_out_of_invoke(self):
        """A node blowing up inside the graph must surface to invoke()'s caller.

        The executor runs as a background task feeding an asyncio.Queue, so an exception
        there could plausibly be swallowed (never retrieved) or deadlock the drain loop.
        The wait_for turns a hang into a failure rather than a stalled test run.
        """
        import main

        with patch("tools.gateway.get_mcp_client", return_value=MagicMock()), patch(
            "agents.intent_identifier.identify", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(asyncio.wait_for(_collect(main.invoke({"prompt": "hello"}, None)), timeout=10))

        self.assertIn("boom", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
