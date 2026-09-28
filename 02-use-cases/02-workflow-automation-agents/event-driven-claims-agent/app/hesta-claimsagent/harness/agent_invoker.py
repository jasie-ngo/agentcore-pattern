"""AgentInvoker — All 7 LLM agent invocations with error handling.

Each agent call returns a safe default on failure (never raises).
"""

from __future__ import annotations

import logging
from typing import Any

from agents import (
    intent_identifier,
    context_manager,
    attachment_validation,
    empathy as empathy_agent,
    writer as writer_agent,
    reviewer_editor,
    disclosure_check,
)

log = logging.getLogger(__name__)


class AgentInvoker:
    """Stateless manager for LLM agent invocations.

    All methods return safe defaults when agents fail, enabling graceful degradation.
    """

    @staticmethod
    async def intent_identifier(inbound: Any) -> dict:
        """AI-001: Classify email intent(s).

        Returns IntentResult (safe default on failure).
        """
        from models import IntentResult, DetectedIntent
        from intents import taxonomy

        try:
            result = await intent_identifier.identify(inbound)
            log.info("intent_identifier succeeded: primary_intent=%s", result.primary_intent_id)
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001
            log.warning("intent_identifier failed: %s", exc)
            return IntentResult(
                intents=[
                    DetectedIntent(
                        intent_id=taxonomy.OTHER_UNKNOWN,
                        confidence=0,
                        rationale="Intent classification failed; routing to human triage.",
                    )
                ],
                primary_intent_id=taxonomy.OTHER_UNKNOWN,
                sender_type="unknown",
                needs_human_triage=True,
            ).model_dump()

    @staticmethod
    async def context_manager(
        inbound: Any,
        intent_result: dict,
        mcp_client: Any | None = None,
        memory_session: Any | None = None,
    ) -> dict:
        """AI-002: Load context and summarize case.

        Returns CaseSummary (safe default on failure).
        """
        from models import CaseSummary

        try:
            identity, cases = await context_manager.lookup(
                inbound, mcp_client, primary_intent_id=intent_result.get("primary_intent_id")
            )
            summary = await context_manager.summarize(
                inbound,
                identity,
                cases,
                history=[],  # history loaded separately by MemoryManager
                session_manager=memory_session,
            )
            log.info("context_manager succeeded: member_id=%s case_id=%s",
                    summary.identity.member_id, summary.cases.case_id)
            return summary.model_dump()
        except Exception as exc:  # noqa: BLE001
            log.warning("context_manager failed: %s", exc)
            return CaseSummary().model_dump()

    @staticmethod
    async def attachment_validation(
        inbound: Any,
        intent_result: dict,
        forms_on_file: dict | None = None,
    ) -> dict | None:
        """AI-004: Validate attachment (if verified member).

        Returns AttachmentAssessment or None (safe default).
        """
        from models import AttachmentAssessment

        try:
            result = attachment_validation.assess(
                inbound,
                intent_result,
                forms_on_file=forms_on_file or {},
            )
            log.info("attachment_validation succeeded: status=%s", result.status)
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001
            log.warning("attachment_validation failed: %s", exc)
            return AttachmentAssessment().model_dump()

    @staticmethod
    async def empathy(inbound: Any, intent_result: dict) -> dict:
        """AI-005: Assess empathy/priority.

        Returns EmpathyAssessment (safe default on failure).
        """
        from models import EmpathyAssessment

        try:
            result = await empathy_agent.assess(inbound)
            log.info("empathy succeeded: priority=%s", result.priority)
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001
            log.warning("empathy failed: %s", exc)
            return EmpathyAssessment().model_dump()

    @staticmethod
    async def writer(
        inbound: Any,
        intent_result: dict,
        profile: dict,
        case_summary: dict,
        empathy_result: dict,
        history: list[dict],
        attachment: dict | None = None,
    ) -> dict:
        """AI-011: Generate draft email.

        Returns DraftEmail (safe default on failure).
        """
        from models import DraftEmail

        try:
            result = await writer_agent.write(
                inbound,
                intent_result,
                profile,
                case_summary,
                empathy_result,
                history,
                attachment=attachment,
            )
            log.info("writer succeeded: subject=%s body_len=%d", result.subject, len(result.body))
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001
            log.warning("writer failed: %s", exc)
            return DraftEmail(
                subject="Unable to generate draft",
                body="The draft generation failed. Please contact support.",
                assumptions=["Draft generation error"],
            ).model_dump()

    @staticmethod
    async def reviewer(
        draft: dict,
        intent_result: dict,
        profile: dict,
        attachment: dict | None = None,
    ) -> dict | None:
        """AI-012: Review draft for issues.

        Returns ReviewResult or None (safe default on failure).
        """
        try:
            result = await reviewer_editor.review(draft, intent_result, profile, attachment=attachment)
            log.info("reviewer succeeded: approved=%s", result.approved_for_human_send)
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001
            log.warning("reviewer failed: %s", exc)
            return None

    @staticmethod
    async def reviewer_revise(
        inbound: Any,
        intent_result: dict,
        profile: dict,
        case_summary: dict,
        empathy_result: dict,
        draft: dict,
        review_result: dict,
        history: list[dict],
        attachment: dict | None = None,
    ) -> dict:
        """AI-011 revision: Improve draft based on reviewer feedback.

        Returns DraftEmail (safe default on failure).
        """
        from models import DraftEmail

        try:
            result = await writer_agent.revise(
                inbound,
                intent_result,
                profile,
                case_summary,
                empathy_result,
                draft,
                review_result,
                history,
                attachment=attachment,
            )
            log.info("writer revise succeeded: subject=%s body_len=%d", result.subject, len(result.body))
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001
            log.warning("writer revise failed: %s", exc)
            return draft  # return unchanged draft on failure

    @staticmethod
    async def disclosure_check(subject: str, body: str) -> list[str]:
        """Check for sensitive account details in draft.

        Returns list of findings (empty list on failure).
        """
        try:
            findings = disclosure_check.scan(subject, body)
            if findings:
                log.info("disclosure_check flagged: %s", findings)
            return findings
        except Exception as exc:  # noqa: BLE001
            log.warning("disclosure_check failed: %s", exc)
            return []
