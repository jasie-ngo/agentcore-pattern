"""Tests for HESTA harness orchestrator.

Tests cover:
- Unit tests for each manager (MemoryManager, ToolCaller, AgentInvoker)
- Integration tests for phases
- Full orchestration scenarios
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# Adjust import paths as needed for your test environment
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness import AgentHarness, OrchestrationResult
from harness.memory_manager import MemoryManager
from harness.tool_caller import ToolCaller
from harness.agent_invoker import AgentInvoker


class TestMemoryManager:
    """Tests for MemoryManager."""

    @pytest.mark.asyncio
    async def test_setup_session_with_verified_member(self):
        """Test memory session setup with verified member."""
        result = await MemoryManager.setup_session("MEM001", "CASE123")
        assert result is not None
        assert result["actor_id"] is not None
        assert result["session_id"] is not None

    @pytest.mark.asyncio
    async def test_setup_session_without_member(self):
        """Test memory session setup without member (graceful)."""
        result = await MemoryManager.setup_session(None, "CASE123")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_history_empty(self):
        """Test loading history with no entries."""
        history = await MemoryManager.load_history("actor123", "session123", "key123")
        assert isinstance(history, list)
        # Returns empty list when no history available (graceful degradation)

    @pytest.mark.asyncio
    async def test_record_turns_empty(self):
        """Test recording empty turns."""
        result = await MemoryManager.record_turns("actor123", "session123", [])
        assert result is False


class TestToolCaller:
    """Tests for ToolCaller."""

    def test_init_with_mcp(self):
        """Test ToolCaller initialization with MCP client."""
        mock_mcp = MagicMock()
        caller = ToolCaller(mock_mcp)
        assert caller.mcp is mock_mcp

    def test_init_without_mcp(self):
        """Test ToolCaller initialization without MCP (graceful degradation)."""
        caller = ToolCaller(None)
        assert caller.mcp is None

    @pytest.mark.asyncio
    async def test_member_lookup_without_mcp(self):
        """Test member lookup gracefully degrades without MCP."""
        caller = ToolCaller(None)
        result = await caller.member_lookup("MEM001", "test@example.com")
        assert result.get("error") is not None

    @pytest.mark.asyncio
    async def test_case_lookup_without_mcp(self):
        """Test case lookup gracefully degrades without MCP."""
        caller = ToolCaller(None)
        result = await caller.case_lookup_creation("MEM001", "Subject")
        assert result.get("status") == "unavailable"

    @pytest.mark.asyncio
    async def test_email_review_without_mcp(self):
        """Test email review returns None without MCP."""
        caller = ToolCaller(None)
        result = await caller.email_review("CASE123", "MEM001", "Subject", "Body", "intent123")
        assert result is None

    @pytest.mark.asyncio
    async def test_case_facts_update_without_mcp(self):
        """Test case facts update returns False without MCP."""
        caller = ToolCaller(None)
        result = await caller.case_facts_update("CASE123", "intent123")
        assert result is False


class TestAgentInvoker:
    """Tests for AgentInvoker."""

    @pytest.mark.asyncio
    async def test_intent_identifier_graceful_failure(self):
        """Test intent identifier returns safe default on failure."""
        with patch("agents.intent_identifier.identify", side_effect=Exception("Test error")):
            result = await AgentInvoker.intent_identifier(MagicMock())
            assert result.get("primary_intent_id") == "other_unknown"
            assert result.get("needs_human_triage") is True

    @pytest.mark.asyncio
    async def test_context_manager_graceful_failure(self):
        """Test context manager returns safe default on failure."""
        with patch("agents.context_manager.lookup", side_effect=Exception("Test error")):
            result = await AgentInvoker.context_manager(MagicMock(), {}, None)
            # Returns empty CaseSummary on failure
            assert result is not None

    @pytest.mark.asyncio
    async def test_attachment_validation_graceful_failure(self):
        """Test attachment validation returns safe default on failure."""
        with patch("agents.attachment_validation.assess", side_effect=Exception("Test error")):
            result = await AgentInvoker.attachment_validation(MagicMock(), {})
            # Returns empty AssessmentAttachment on failure
            assert result is not None

    @pytest.mark.asyncio
    async def test_empathy_graceful_failure(self):
        """Test empathy assessment returns safe default on failure."""
        with patch("agents.empathy_agent.assess", side_effect=Exception("Test error")):
            result = await AgentInvoker.empathy(MagicMock(), {})
            assert result.get("sentiment") == "neutral"

    @pytest.mark.asyncio
    async def test_disclosure_check_graceful_failure(self):
        """Test disclosure check returns empty list on failure."""
        with patch("agents.disclosure_check.scan", side_effect=Exception("Test error")):
            result = await AgentInvoker.disclosure_check("Subject", "Body")
            assert result == []


class TestAgentHarness:
    """Tests for AgentHarness orchestrator."""

    def test_init_with_mcp(self):
        """Test harness initialization with MCP."""
        mock_mcp = MagicMock()
        harness = AgentHarness(mock_mcp)
        assert harness.mcp is mock_mcp
        assert harness.memory is not None
        assert harness.tools is not None
        assert harness.agents is not None

    def test_init_without_mcp(self):
        """Test harness initialization without MCP (graceful degradation)."""
        harness = AgentHarness(None)
        assert harness.mcp is None
        assert harness.memory is not None

    @pytest.mark.asyncio
    async def test_orchestrate_without_mcp(self):
        """Test orchestration works without MCP (graceful degradation)."""
        harness = AgentHarness(None)
        mock_inbound = MagicMock()

        # Mock the agents to return valid results
        with patch.object(AgentInvoker, "intent_identifier", new_callable=AsyncMock) as mock_intent:
            mock_intent.return_value = {
                "primary_intent_id": "account_inquiry",
                "sender_type": "member",
                "intents": [],
                "personal_advice_requested": False,
                "needs_human_triage": False,
            }

            result = await harness.orchestrate(mock_inbound, "key123")

            # Should complete without errors
            assert isinstance(result, OrchestrationResult)
            assert result.idempotency_key == "key123"
            assert result.intent_result is not None

    @pytest.mark.asyncio
    async def test_orchestrate_personal_advice_path(self):
        """Test orchestration special path for personal advice."""
        harness = AgentHarness(None)
        mock_inbound = MagicMock()

        # Mock agents for personal advice path
        with patch.object(AgentInvoker, "intent_identifier", new_callable=AsyncMock) as mock_intent, \
             patch.object(AgentInvoker, "context_manager", new_callable=AsyncMock) as mock_context, \
             patch.object(AgentInvoker, "writer", new_callable=AsyncMock) as mock_writer:

            mock_intent.return_value = {
                "primary_intent_id": "personal_advice_requested",
                "sender_type": "member",
                "personal_advice_requested": True,
                "intents": [],
                "needs_human_triage": False,
            }

            mock_context.return_value = {
                "identity": {"member_id": "MEM001", "email": "test@example.com"},
                "cases": {"case_id": "CASE123"},
                "summary": "",
                "conversation_state": "new",
                "outstanding_items": [],
            }

            mock_writer.return_value = {
                "subject": "Re: Your Inquiry",
                "body": "We cannot provide personal financial advice.",
                "assumptions": [],
            }

            result = await harness.orchestrate(mock_inbound, "key123")

            # Should take personal advice path
            assert result.personal_advice_path is True
            assert result.routing_decision.get("escalate_to_human") is True
            assert result.draft is not None


class TestOrchestrationResult:
    """Tests for OrchestrationResult model."""

    def test_result_initialization(self):
        """Test OrchestrationResult initialization."""
        result = OrchestrationResult(idempotency_key="key123")
        assert result.idempotency_key == "key123"
        assert result.errors == []
        assert result.memory_recorded is False
        assert result.revision_count == 0

    def test_result_with_all_fields(self):
        """Test OrchestrationResult with populated fields."""
        result = OrchestrationResult(
            idempotency_key="key123",
            intent_result={"primary_intent_id": "account_inquiry"},
            draft={"subject": "Test", "body": "Test body"},
            memory_recorded=True,
            errors=["Test error"],
        )
        assert result.intent_result["primary_intent_id"] == "account_inquiry"
        assert result.draft["subject"] == "Test"
        assert result.memory_recorded is True
        assert len(result.errors) == 1

    def test_result_serialization(self):
        """Test OrchestrationResult can be serialized to JSON."""
        result = OrchestrationResult(
            idempotency_key="key123",
            intent_result={"primary_intent_id": "account_inquiry"},
        )
        # Should be serializable
        json_str = json.dumps(result.model_dump(), default=str)
        assert "key123" in json_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
