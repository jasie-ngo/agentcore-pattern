"""fabric.adapters: each node reads the right state keys, returns the right update dict
(ADR-0015 decisions 1, 3). No agent logic is exercised — everything is patched."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from fabric import adapters, registry  # noqa: E402,F401 — import registers the adapters


def _run(coro):
    return asyncio.run(coro)


class AdapterTests(unittest.TestCase):
    def test_intent_identifier_adapter(self):
        adapter = registry.AGENT_NODES["intent_identifier"]
        with patch("agents.intent_identifier.identify", new=AsyncMock(return_value="INTENT")):
            update = _run(adapter({"inbound": "INBOUND"}))
        self.assertEqual(update, {"intent_result": "INTENT"})

    def test_context_manager_adapter_without_memory(self):
        adapter = registry.AGENT_NODES["context_manager"]
        summary = type("S", (), {"summary": "SUMMARY TEXT"})()
        with patch("agents.context_manager.summarize", new=AsyncMock(return_value=summary)):
            update = _run(adapter({"inbound": "INBOUND", "memory_session": None}))
        self.assertEqual(update, {"summary": summary, "memory_recorded": None})

    def test_context_manager_adapter_with_memory(self):
        adapter = registry.AGENT_NODES["context_manager"]
        inbound = type("I", (), {"latest_message": "hi"})()
        summary = type("S", (), {"summary": "SUMMARY TEXT"})()
        state = {"inbound": inbound, "memory_session": object(), "actor_id": "a1", "session_id": "s1"}
        with patch("agents.context_manager.summarize", new=AsyncMock(return_value=summary)), patch(
            "memory.session.record_interaction", return_value=True
        ) as record:
            update = _run(adapter(state))
        record.assert_called_once_with("a1", "s1", "hi", "SUMMARY TEXT")
        self.assertEqual(update, {"summary": summary, "memory_recorded": True})

    def test_empathy_adapter(self):
        adapter = registry.AGENT_NODES["empathy"]
        with patch("agents.empathy.assess", new=AsyncMock(return_value="EMPATHY")):
            update = _run(adapter({"inbound": "INBOUND"}))
        self.assertEqual(update, {"empathy": "EMPATHY"})

    def test_writer_adapter(self):
        adapter = registry.AGENT_NODES["writer"]
        state = {
            "inbound": "INBOUND",
            "intent_result": "INTENT",
            "profile": "PROFILE",
            "summary": "SUMMARY",
            "empathy": "EMPATHY",
            "status_ctx": "STATUS",
        }
        with patch("agents.writer.write", new=AsyncMock(return_value="DRAFT")) as write:
            update = _run(adapter(state))
        write.assert_called_once_with("INBOUND", "INTENT", "PROFILE", "SUMMARY", "EMPATHY", status_ctx="STATUS")
        self.assertEqual(update, {"draft": "DRAFT"})

    def test_reviewer_editor_adapter(self):
        adapter = registry.AGENT_NODES["reviewer_editor"]
        state = {"draft": "DRAFT", "intent_result": "INTENT", "profile": "PROFILE"}
        with patch("agents.reviewer_editor.review", new=AsyncMock(return_value="REVIEW")):
            update = _run(adapter(state))
        self.assertEqual(update, {"review": "REVIEW"})

    def test_identity_profiling_adapter(self):
        adapter = registry.DETERMINISTIC_NODES["identity_profiling"]
        state = {"mcp": "MCP", "inbound": "INBOUND", "intent_result": "INTENT"}
        with patch("agents.identity_profiling.profile", new=AsyncMock(return_value="PROFILE")):
            update = _run(adapter(state))
        self.assertEqual(update, {"profile": "PROFILE"})

    def test_attachment_validation_adapter(self):
        adapter = registry.DETERMINISTIC_NODES["attachment_validation"]
        state = {"inbound": "INBOUND", "intent_result": "INTENT"}
        with patch("agents.attachment_validation.assess", return_value="ATTACH"):
            update = adapter(state)
        self.assertEqual(update, {"attachments": "ATTACH"})

    def test_case_status_lookup_adapter(self):
        adapter = registry.DETERMINISTIC_NODES["case_status_lookup"]
        state = {"mcp": "MCP", "inbound": "INBOUND"}
        with patch("agents.case_status.lookup_pending", new=AsyncMock(return_value="STATUS")):
            update = _run(adapter(state))
        self.assertEqual(update, {"status_ctx": "STATUS"})

    def test_routing_decision_adapter(self):
        adapter = registry.DETERMINISTIC_NODES["routing_decision"]
        state = {"intent_result": "INTENT", "profile": "PROFILE", "empathy": "EMPATHY"}
        with patch("routing.decide", return_value="DECISION"):
            update = adapter(state)
        self.assertEqual(update, {"decision": "DECISION"})

    def test_hitl_record_adapter_disabled(self):
        adapter = registry.DETERMINISTIC_NODES["hitl_record"]
        decision = type("D", (), {"reasons": ["low confidence"]})()
        with patch("config.ENABLE_HITL_RECORD", False):
            update = _run(adapter({"decision": decision}))
        self.assertIn("HITL record disabled", update["hitl_message"])

    def test_hitl_record_adapter_enabled(self):
        adapter = registry.DETERMINISTIC_NODES["hitl_record"]
        state = {
            "mcp": "MCP",
            "inbound": "INBOUND",
            "intent_result": "INTENT",
            "profile": "PROFILE",
            "decision": "DECISION",
            "draft": "DRAFT",
        }
        with patch("config.ENABLE_HITL_RECORD", True), patch(
            "hitl.write_hitl_record", new=AsyncMock(return_value="MESSAGE")
        ) as write:
            update = _run(adapter(state))
        write.assert_called_once_with("MCP", "INBOUND", "INTENT", "PROFILE", "DECISION", "DRAFT")
        self.assertEqual(update, {"hitl_message": "MESSAGE"})


if __name__ == "__main__":
    unittest.main()
