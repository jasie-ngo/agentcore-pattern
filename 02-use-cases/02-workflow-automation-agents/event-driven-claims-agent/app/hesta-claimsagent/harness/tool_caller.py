"""ToolCaller — MCP Gateway tool invocation with graceful degradation.

All tool calls return safe defaults on failure. Methods never raise exceptions.
Supports being called with mcp=None (graceful degradation when Gateway unavailable).
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


class ToolCaller:
    """Stateless manager for MCP Gateway tool calls.

    Each tool returns a safe default on failure, enabling the orchestrator
    to continue when the Gateway or a specific tool is unavailable.
    """

    def __init__(self, mcp_client: Any | None = None):
        """Initialize with an optional MCPClient.

        Args:
            mcp_client: MCPClient instance, or None for graceful degradation
        """
        self.mcp = mcp_client

    async def member_lookup(self, member_number: str | None, email: str | None) -> dict:
        """Look up member identity via MCP Gateway.

        Returns safe default (unverified) if lookup fails or MCP unavailable.
        """
        from models import IdentityInfo

        if not self.mcp:
            log.debug("member_lookup: MCP unavailable, returning unverified")
            return IdentityInfo(error="Gateway unavailable").model_dump()

        try:
            result = await self.mcp.call_tool_async(
                "member-lookup___member_lookup",
                {"member_number": member_number, "email": email},
            )
            log.info("member_lookup succeeded: member_id=%s", result.get("member_id"))
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("member_lookup failed: %s", exc)
            return IdentityInfo(error=f"lookup failed: {exc}").model_dump()

    async def case_lookup_creation(self, member_id: str | None, subject: str) -> dict:
        """Look up or create a case via MCP Gateway.

        Returns safe default (unavailable) if lookup fails or MCP unavailable.
        """
        from models import CaseInfo

        if not self.mcp:
            log.debug("case_lookup_creation: MCP unavailable, returning unavailable")
            return CaseInfo(status="unavailable", error="Gateway unavailable").model_dump()

        try:
            result = await self.mcp.call_tool_async(
                "case-lookup-creation___case_lookup_creation",
                {"member_id": member_id, "subject": subject},
            )
            log.info("case_lookup_creation succeeded: case_id=%s status=%s", result.get("case_id"), result.get("status"))
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("case_lookup_creation failed: %s", exc)
            return CaseInfo(status="unavailable", error=f"lookup failed: {exc}").model_dump()

    async def email_review(
        self,
        case_id: str | None,
        member_id: str | None,
        draft_subject: str,
        draft_body: str,
        intent_id: str,
        escalation_reason: str | None = None,
    ) -> str | None:
        """Write a human-in-the-loop review record via MCP Gateway.

        Returns the HITL record ID on success, None on failure.
        Never raises.
        """
        if not self.mcp:
            log.debug("email_review: MCP unavailable, skipping HITL record")
            return None

        try:
            result = await self.mcp.call_tool_async(
                "email-review___email_review",
                {
                    "case_id": case_id,
                    "member_id": member_id,
                    "draft_subject": draft_subject,
                    "draft_body": draft_body,
                    "intent_id": intent_id,
                    "escalation_reason": escalation_reason,
                },
            )
            record_id = result.get("record_id") if isinstance(result, dict) else str(result)
            log.info("email_review succeeded: record_id=%s", record_id)
            return record_id
        except Exception as exc:  # noqa: BLE001
            log.warning("email_review failed: %s", exc)
            return None

    async def case_facts_update(
        self,
        case_id: str | None,
        intent_id: str,
        attachment_status: str | None = None,
    ) -> bool:
        """Update case facts (e.g., attachment status) via MCP Gateway.

        Returns True on success, False on failure. Never raises.
        """
        if not self.mcp:
            log.debug("case_facts_update: MCP unavailable, skipping case update")
            return False

        try:
            await self.mcp.call_tool_async(
                "case-facts-update___case_facts_update",
                {
                    "case_id": case_id,
                    "intent_id": intent_id,
                    "attachment_status": attachment_status,
                },
            )
            log.info("case_facts_update succeeded: case_id=%s intent_id=%s", case_id, intent_id)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("case_facts_update failed: %s", exc)
            return False
