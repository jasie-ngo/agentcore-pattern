"""Loads and validates the real HESTA fabric config (ADR-0015 decisions 1 and 7)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from fabric.loader import load_fabric_config  # noqa: E402

_WORKFLOW_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "hesta-claimsagent", "workflows", "hesta.workflow.yaml"
)

_EXPECTED_NODE_IDS = {
    "intent_identifier",
    "context_manager",
    "case_status_lookup",
    "identity_profiling",
    "attachment_validation",
    "empathy",
    "routing_decision",
    "writer",
    "reviewer_editor",
    "hitl_record",
}


class HestaWorkflowYamlTests(unittest.TestCase):
    def test_loads_and_validates(self):
        config = load_fabric_config(_WORKFLOW_PATH)
        self.assertEqual(config.workflow.start, "intent_identifier")
        self.assertEqual({n.id for n in config.workflow.nodes}, _EXPECTED_NODE_IDS)

    def test_member_facing_writer_agents_are_guarded(self):
        config = load_fabric_config(_WORKFLOW_PATH)
        member_facing = [a for a in config.agents.values() if a.role == "member_facing_writer"]
        self.assertEqual({a.name for a in member_facing}, {"writer", "reviewer_editor"})
        self.assertTrue(all(a.guarded for a in member_facing))

    def test_conditional_edges_reference_registered_routers(self):
        config = load_fabric_config(_WORKFLOW_PATH)
        routed = {e.router for e in config.workflow.edges if e.router}
        self.assertEqual(routed, {"is_status_query", "escalate_to_human"})


if __name__ == "__main__":
    unittest.main()
