"""fabric.routers: router predicates gate optional graph edges (ADR-0015 decisions 1
and 3). No agent logic is exercised — everything is patched."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from fabric import registry  # noqa: E402
from fabric import routers  # noqa: E402,F401 — import registers the routers


class RouterTests(unittest.TestCase):
    def setUp(self):
        registry.reset()

    def tearDown(self):
        registry.reset()

    def test_is_status_query_delegates_to_case_status(self):
        router = registry.ROUTERS["is_status_query"]
        state = {"inbound": "INBOUND", "summary": "SUMMARY", "intent_result": "INTENT"}
        with patch("agents.case_status.is_status_query", return_value=True) as is_status_query:
            result = router(state)
        is_status_query.assert_called_once_with("INBOUND", "SUMMARY", "INTENT")
        self.assertTrue(result)

    def test_escalate_to_human_reads_decision(self):
        router = registry.ROUTERS["escalate_to_human"]

        decision = type("Decision", (), {"escalate_to_human": True})()
        self.assertTrue(router({"decision": decision}))

        decision = type("Decision", (), {"escalate_to_human": False})()
        self.assertFalse(router({"decision": decision}))


if __name__ == "__main__":
    unittest.main()
