"""Unit tests for the deterministic Phase 3 tool in app/claims_agent/tools.py.

``execute_claim_decision`` is exercised against a fake Gateway MCP client, so no
AWS or LLM is needed. The tool requires the Strands SDK (``@tool`` decorator),
so the whole module is skipped when Strands isn't installed (e.g., running
``python3 -m unittest`` outside the agent venv). Run under the agent venv
(``app/.venv``) to exercise it.

Run:
    app/.venv/bin/python -m unittest discover -s tests
"""

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

try:
    from claims_agent import tools

    _STRANDS_AVAILABLE = True
except ImportError:
    _STRANDS_AVAILABLE = False


class _FakeGateway:
    """Stands in for MCPClient: records calls, returns canned Gateway results."""

    def __init__(self, failing_tools=(), existing_claim=False):
        self.calls = []
        self.failing_tools = set(failing_tools)
        self.existing_claim = existing_claim

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    async def call_tool_async(self, tool_use_id, name, arguments):
        self.calls.append((name, arguments))
        if name in self.failing_tools:
            return {"status": "error", "toolUseId": tool_use_id, "content": [{"text": "denied by policy"}]}
        payload = {"ok": True}
        if name.endswith("create_claim"):
            payload = {"claim_id": "CLM-TEST1", "result": "existing" if self.existing_claim else "created"}
        return {"status": "success", "toolUseId": tool_use_id, "content": [{"text": json.dumps(payload)}]}


@unittest.skipUnless(_STRANDS_AVAILABLE, "Strands SDK not installed (run under agent venv)")
class ExecuteClaimDecisionTests(unittest.TestCase):
    def _run(self, gateway, **overrides):
        args = {
            "decision": "ACCEPT",
            "policy_number": "POL-12345",
            "amount": 5000,
            "category": "auto_collision",
            "description": "Rear-ended at a stop light",
            "reasoning": "Policy active, within limits",
            "confidence": 92,
            "routing": "AUTO_APPROVE",
            "concerns": "None",
            "claimant_email": "john@example.com",
        }
        args.update(overrides)
        original = tools.build_gateway_client
        tools.build_gateway_client = lambda: gateway
        try:
            return asyncio.run(tools.execute_claim_decision(**args))
        finally:
            tools.build_gateway_client = original

    def _called(self, gateway):
        return [name.split("___")[-1] for name, _ in gateway.calls]

    def test_auto_approve_creates_approved_claim_and_notifies(self):
        gw = _FakeGateway()
        summary = self._run(gw)
        self.assertEqual(self._called(gw), ["create_claim", "send_notification"])
        self.assertEqual(gw.calls[0][1]["status"], "approved")
        self.assertIn("Action: AUTO_APPROVE", summary)
        self.assertIn("Claim created: CLM-TEST1", summary)

    def test_reject_only_notifies(self):
        gw = _FakeGateway()
        summary = self._run(gw, decision="REJECT")
        self.assertEqual(self._called(gw), ["send_notification"])
        self.assertIn("Action: REJECT", summary)

    def test_human_review_creates_pending_claim_and_escalates(self):
        gw = _FakeGateway()
        summary = self._run(gw, confidence=55, routing="HUMAN_REVIEW")
        self.assertEqual(self._called(gw), ["create_claim", "request_human_review", "send_notification"])
        self.assertEqual(gw.calls[0][1]["status"], "pending_review")
        self.assertEqual(gw.calls[1][1]["claim_id"], "CLM-TEST1")
        self.assertIn("Action: HUMAN_REVIEW", summary)

    def test_auto_approve_below_threshold_is_overridden(self):
        gw = _FakeGateway()
        summary = self._run(gw, confidence=60, routing="AUTO_APPROVE")
        self.assertIn("Action: HUMAN_REVIEW", summary)

    def test_missing_decision_defaults_to_reject(self):
        gw = _FakeGateway()
        self.assertIn("Action: REJECT", self._run(gw, decision=""))

    def test_missing_validation_defaults_to_human_review(self):
        gw = _FakeGateway()
        self.assertIn("Action: HUMAN_REVIEW", self._run(gw, routing="", confidence=0))

    def test_no_claimant_email_skips_notification(self):
        gw = _FakeGateway()
        self._run(gw, claimant_email="")
        self.assertEqual(self._called(gw), ["create_claim"])

    def test_gateway_error_is_non_fatal_and_skips_escalation(self):
        # e.g. the Cedar BlockExcessiveClaims policy denies create_claim.
        gw = _FakeGateway(failing_tools={"create-claim___create_claim"})
        summary = self._run(gw, confidence=55, routing="HUMAN_REVIEW")
        self.assertIn("Could not call create_claim: denied by policy", summary)
        self.assertEqual(self._called(gw), ["create_claim", "send_notification"])

    def test_gateway_unavailable_makes_no_calls(self):
        summary = self._run(None)
        self.assertIn("Gateway unavailable", summary)

    def test_source_is_passed_to_create_claim(self):
        gw = _FakeGateway()
        self._run(gw, source="s3://b/claims-inbox/x.eml")
        self.assertEqual(gw.calls[0][1]["source"], "s3://b/claims-inbox/x.eml")

    def test_no_source_is_not_sent(self):
        gw = _FakeGateway()
        self._run(gw)
        self.assertNotIn("source", gw.calls[0][1])

    def test_redelivered_claim_skips_review_and_notification(self):
        gw = _FakeGateway(existing_claim=True)
        summary = self._run(gw, confidence=55, routing="HUMAN_REVIEW", source="s3://b/k")
        self.assertEqual(self._called(gw), ["create_claim"])
        self.assertIn("duplicate delivery", summary)

    def test_redelivered_approval_skips_notification(self):
        gw = _FakeGateway(existing_claim=True)
        self._run(gw, source="s3://b/k")
        self.assertEqual(self._called(gw), ["create_claim"])


if __name__ == "__main__":
    unittest.main()
