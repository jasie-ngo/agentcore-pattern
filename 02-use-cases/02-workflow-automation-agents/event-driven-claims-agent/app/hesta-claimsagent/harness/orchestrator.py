"""AgentHarness — Main orchestrator for the 4-phase HESTA pipeline.

Coordinates all managers (MemoryManager, ToolCaller, AgentInvoker) to execute:
  UNDERSTAND: Intent + Context
  DECIDE: Identity + Attachments + Empathy + Routing
  EXECUTE: Draft + Review loop + HITL
  LEARN: Observability (via existing OTEL traces)

Special path: Personal advice requests always escalate with hardcoded decline draft.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from config import MAX_DRAFT_REVISIONS
from routing import decide
from intents import taxonomy

from harness.memory_manager import MemoryManager
from harness.tool_caller import ToolCaller
from harness.agent_invoker import AgentInvoker
from harness.models import OrchestrationResult

log = logging.getLogger(__name__)


class AgentHarness:
    """Stateless orchestrator for the HESTA 4-phase pipeline.

    All state is passed as input; all results returned as OrchestrationResult.
    """

    def __init__(self, mcp_client: Any | None = None):
        """Initialize harness with optional MCP client.

        Args:
            mcp_client: MCPClient instance or None for graceful degradation
        """
        self.mcp = mcp_client
        self.memory = MemoryManager()
        self.tools = ToolCaller(mcp_client)
        self.agents = AgentInvoker()

    async def orchestrate(
        self,
        inbound: Any,
        idempotency_key: str,
    ) -> OrchestrationResult:
        """Execute full 4-phase orchestration.

        Args:
            inbound: Normalized email object
            idempotency_key: Unique key for deduplication

        Returns:
            OrchestrationResult with all phase outputs
        """
        result = OrchestrationResult(idempotency_key=idempotency_key)

        # ── PHASE 1: UNDERSTAND ──────────────────────────────────────────
        try:
            intent_data = await self.agents.intent_identifier(inbound)
            result.intent_result = intent_data
            log.info("Phase 1 UNDERSTAND: intent=%s", intent_data.get("primary_intent_id"))

            # Personal advice path: skip all LLM steps below
            if intent_data.get("personal_advice_requested"):
                result.personal_advice_path = True
                return await self._phase_personal_advice(inbound, intent_data, idempotency_key, result)

            # Load case context + history
            case_data = await self.agents.context_manager(inbound, intent_data, self.mcp)
            result.case_summary = case_data

            # Setup Memory session if verified member + case
            memory_session = None
            history = []
            if case_data.get("identity", {}).get("member_id") and case_data.get("cases", {}).get("case_id"):
                mem_info = await self.memory.setup_session(
                    case_data["identity"]["member_id"],
                    case_data["cases"]["case_id"],
                )
                if mem_info:
                    memory_session = mem_info.get("session_manager")
                    result.memory_error = mem_info.get("error")

                    # Load history with idempotency filtering
                    history = await self.memory.load_history(
                        mem_info["actor_id"],
                        mem_info["session_id"],
                        idempotency_key,
                    )
                    log.debug("Loaded %d history entries for %s", len(history), mem_info["session_id"])

        except Exception as exc:  # noqa: BLE001
            log.error("Phase 1 UNDERSTAND failed: %s", exc)
            result.errors.append(f"Phase 1 failed: {exc}")
            return result

        # ── PHASE 2: DECIDE ──────────────────────────────────────────────
        try:
            verified_member = bool(result.case_summary.get("identity", {}).get("member_id"))

            # Convert identity to profile
            from harness.utils import convert_identity_to_profile
            profile = convert_identity_to_profile(
                result.case_summary.get("identity"),
                inbound,
                result.intent_result,
            )
            result.member_profile = profile

            # Attachment assessment (only for verified members)
            if verified_member:
                attach = await self.agents.attachment_validation(
                    inbound,
                    result.intent_result,
                    forms_on_file=result.case_summary.get("cases", {}).get("forms_on_file"),
                )
                result.attachment_result = attach
            else:
                result.attachment_result = None

            # Empathy assessment (only for verified members)
            if verified_member:
                emp = await self.agents.empathy(inbound, result.intent_result)
                result.empathy_result = emp
            else:
                from models import EmpathyAssessment
                result.empathy_result = EmpathyAssessment(
                    sentiment="neutral",
                    priority="normal",
                    recommended_attention="Identity verification required",
                ).model_dump()

            # Routing decision
            routing = decide(
                result.intent_result,
                result.member_profile,
                result.empathy_result,
            )
            result.routing_decision = routing

            # Escalate if attachment unreadable/wrong form
            if result.attachment_result and result.attachment_result.get("status") in ("unreadable", "wrong_form"):
                routing["reasons"].append(f"attachment {result.attachment_result['status']}: {result.attachment_result.get('notes')}")
                routing["escalate_to_human"] = True

            log.info("Phase 2 DECIDE: escalate=%s", routing.get("escalate_to_human"))

        except Exception as exc:  # noqa: BLE001
            log.error("Phase 2 DECIDE failed: %s", exc)
            result.errors.append(f"Phase 2 failed: {exc}")
            return result

        # ── PHASE 3: EXECUTE ─────────────────────────────────────────────
        try:
            # Generate draft
            draft = await self.agents.writer(
                inbound,
                result.intent_result,
                result.member_profile,
                result.case_summary,
                result.empathy_result,
                history,
                attachment=result.attachment_result,
            )
            result.draft = draft
            log.info("Phase 3 EXECUTE: draft generated, subject=%s", draft.get("subject"))

            # Revision loop (only for verified members)
            review = None
            revision_count = 0
            if verified_member:
                review = await self.agents.reviewer(
                    result.draft,
                    result.intent_result,
                    result.member_profile,
                    attachment=result.attachment_result,
                )
                result.review_result = review

                # Revise draft if not approved
                while (
                    review is not None
                    and not review.get("approved_for_human_send")
                    and revision_count < MAX_DRAFT_REVISIONS
                ):
                    revision_count += 1
                    draft = await self.agents.reviewer_revise(
                        inbound,
                        result.intent_result,
                        result.member_profile,
                        result.case_summary,
                        result.empathy_result,
                        result.draft,
                        review,
                        history,
                        attachment=result.attachment_result,
                    )
                    result.draft = draft
                    review = await self.agents.reviewer(
                        result.draft,
                        result.intent_result,
                        result.member_profile,
                        attachment=result.attachment_result,
                    )
                    result.review_result = review

            result.revision_count = revision_count

            # Escalate if review not approved
            if review is not None and not review.get("approved_for_human_send"):
                result.routing_decision["reasons"].append(
                    f"draft not approved after {revision_count} revision(s)" if revision_count
                    else "draft not approved for human send"
                )
                result.routing_decision["escalate_to_human"] = True
            elif review is None and verified_member:
                result.routing_decision["reasons"].append("automated review unavailable")
                result.routing_decision["escalate_to_human"] = True

            # Disclosure check
            findings = await self.agents.disclosure_check(result.draft.get("subject", ""), result.draft.get("body", ""))
            if findings:
                result.disclosure_result = {"findings": findings, "has_sensitive_details": True}
                result.routing_decision["reasons"].append(f"sensitive details: {'; '.join(findings)}")
                result.routing_decision["escalate_to_human"] = True
            else:
                result.disclosure_result = {"findings": [], "has_sensitive_details": False}

            log.info("Phase 3 EXECUTE: complete, revisions=%d escalate=%s", revision_count, result.routing_decision.get("escalate_to_human"))

        except Exception as exc:  # noqa: BLE001
            log.error("Phase 3 EXECUTE failed: %s", exc)
            result.errors.append(f"Phase 3 failed: {exc}")
            return result

        # ── PHASE 4: LEARN ───────────────────────────────────────────────
        try:
            # Record to Memory (if verified member)
            verified_member = bool(result.case_summary.get("identity", {}).get("member_id"))
            if verified_member and result.case_summary.get("cases", {}).get("case_id"):
                mem_info = await self.memory.setup_session(
                    result.case_summary["identity"]["member_id"],
                    result.case_summary["cases"]["case_id"],
                )
                if mem_info:
                    history_entries = self._build_history_entries(result, idempotency_key)
                    recorded = await self.memory.record_turns(
                        mem_info["actor_id"],
                        mem_info["session_id"],
                        history_entries,
                    )
                    result.memory_recorded = recorded
                    if not recorded and mem_info.get("error"):
                        result.memory_failed = True
                        result.memory_error = mem_info["error"]
                        log.warning("Memory recording failed: %s", mem_info["error"])

            # Update case facts
            if verified_member and self.mcp:
                await self.tools.case_facts_update(
                    result.case_summary["cases"]["case_id"],
                    result.intent_result["primary_intent_id"],
                    attachment_status=result.attachment_result.get("status") if result.attachment_result else None,
                )

            # Write HITL record if escalating
            if result.routing_decision.get("escalate_to_human") and self.mcp:
                record_id = await self.tools.email_review(
                    result.case_summary["cases"]["case_id"],
                    result.case_summary["identity"]["member_id"],
                    result.draft.get("subject", ""),
                    result.draft.get("body", ""),
                    result.intent_result["primary_intent_id"],
                    escalation_reason="; ".join(result.routing_decision.get("reasons", [])),
                )
                result.hitl_record_id = record_id

            log.info("Phase 4 LEARN: complete, memory_recorded=%s hitl_record=%s", result.memory_recorded, result.hitl_record_id)

        except Exception as exc:  # noqa: BLE001
            log.error("Phase 4 LEARN failed: %s", exc)
            result.errors.append(f"Phase 4 failed: {exc}")

        return result

    async def _phase_personal_advice(
        self,
        inbound: Any,
        intent_result: dict,
        idempotency_key: str,
        result: OrchestrationResult,
    ) -> OrchestrationResult:
        """Special path: Personal advice requested.

        Skip all LLM reasoning; always escalate with hardcoded decline draft.
        """
        from agents import writer as writer_agent

        try:
            # Identity + case lookup (deterministic)
            case_data = await self.agents.context_manager(inbound, intent_result, self.mcp)
            result.case_summary = case_data

            # Convert to profile
            from harness.utils import convert_identity_to_profile
            profile = convert_identity_to_profile(
                case_data.get("identity"),
                inbound,
                intent_result,
            )
            result.member_profile = profile

            # Hardcoded decline draft
            decline_draft = writer_agent.advice_decline_draft(inbound, intent_result, profile)
            result.draft = decline_draft.model_dump()

            # Always escalate
            result.routing_decision = {
                "escalate_to_human": True,
                "reasons": ["PERSONAL ADVICE requested — do NOT provide personal financial advice"],
                "regulated": taxonomy.is_regulated(intent_result.get("primary_intent_id")),
            }

            # Record to Memory
            verified_member = bool(case_data.get("identity", {}).get("member_id"))
            if verified_member and case_data.get("cases", {}).get("case_id"):
                mem_info = await self.memory.setup_session(
                    case_data["identity"]["member_id"],
                    case_data["cases"]["case_id"],
                )
                if mem_info:
                    history_entries = self._build_history_entries(result, idempotency_key)
                    recorded = await self.memory.record_turns(
                        mem_info["actor_id"],
                        mem_info["session_id"],
                        history_entries,
                    )
                    result.memory_recorded = recorded

            # Write HITL record
            if self.mcp:
                record_id = await self.tools.email_review(
                    case_data["cases"]["case_id"],
                    case_data["identity"]["member_id"],
                    result.draft.get("subject", ""),
                    result.draft.get("body", ""),
                    intent_result["primary_intent_id"],
                    escalation_reason="Personal advice requested",
                )
                result.hitl_record_id = record_id

            log.info("Personal advice path: complete, hitl_record=%s", result.hitl_record_id)

        except Exception as exc:  # noqa: BLE001
            log.error("Personal advice path failed: %s", exc)
            result.errors.append(f"Personal advice path failed: {exc}")

        return result

    @staticmethod
    def _build_history_entries(result: OrchestrationResult, idempotency_key: str) -> list[dict]:
        """Build Memory history entries from orchestration result."""
        entries = []

        # Inbound entry
        entries.append({
            "entry_id": f"{idempotency_key}:inbound",
            "entry_type": "inbound_member_email",
            "content": "",  # inbound.latest_message (would need to pass inbound)
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "hesta-v2",
            "intent_id": result.intent_result.get("primary_intent_id") if result.intent_result else None,
            "attachments_present": result.attachment_result.get("attachments_present", 0) if result.attachment_result else 0,
        })

        # Draft entry
        if result.draft:
            entries.append({
                "entry_id": f"{idempotency_key}:draft",
                "entry_type": "generated_draft",
                "content": json.dumps({"subject": result.draft.get("subject"), "body": result.draft.get("body")}),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "pipeline_version": "hesta-v2",
                "intent_id": result.intent_result.get("primary_intent_id") if result.intent_result else None,
            })

        # Review entry (if performed)
        if result.review_result:
            entries.append({
                "entry_id": f"{idempotency_key}:review",
                "entry_type": "review_outcome",
                "content": json.dumps({**result.review_result, "revision_count": result.revision_count}),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "pipeline_version": "hesta-v2",
                "intent_id": result.intent_result.get("primary_intent_id") if result.intent_result else None,
            })

        return entries
