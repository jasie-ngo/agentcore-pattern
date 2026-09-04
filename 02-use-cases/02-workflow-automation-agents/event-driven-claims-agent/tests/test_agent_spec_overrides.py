"""Agent modules resolve model/guardrail config via fabric.registry (ADR-0015 decision 1),
and Reviewer & Editor / Writer default to guarded=True even unbound (decision 7 gap fix)."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from fabric import registry  # noqa: E402
from fabric.schema import AgentSpec, FabricConfig, NodeSpec, WorkflowSpec  # noqa: E402


def _bind_single(name: str, **overrides) -> None:
    spec = AgentSpec(name=name, implementation=name, **overrides)
    node = NodeSpec(id=name, type="agent", implementation=name)
    workflow = WorkflowSpec(start=name, nodes=(node,), edges=())
    registry.bind(FabricConfig(agents={name: spec}, workflow=workflow))


class AgentSpecOverrideTests(unittest.TestCase):
    def setUp(self):
        registry.reset()

    def tearDown(self):
        registry.reset()

    def test_intent_identifier_uses_bound_overrides(self):
        import agents.intent_identifier as mod

        _bind_single("intent_identifier", fast=False, guarded=True)
        mod._agent = None
        with patch("agents.intent_identifier.build_agent") as build_agent:
            mod._get()
        _, kwargs = build_agent.call_args
        self.assertFalse(kwargs["fast"])
        self.assertTrue(kwargs["guarded"])

    def test_intent_identifier_defaults_when_unbound(self):
        import agents.intent_identifier as mod

        mod._agent = None
        with patch("agents.intent_identifier.build_agent") as build_agent:
            mod._get()
        _, kwargs = build_agent.call_args
        self.assertTrue(kwargs["fast"])
        self.assertFalse(kwargs["guarded"])

    def test_empathy_defaults_fast_when_unbound(self):
        import agents.empathy as mod

        mod._agent = None
        with patch("agents.empathy.build_agent") as build_agent:
            mod._get()
        _, kwargs = build_agent.call_args
        self.assertTrue(kwargs["fast"])

    def test_context_manager_defaults_not_fast_when_unbound(self):
        import agents.context_manager as mod

        mod._agent = None
        with patch("agents.context_manager.build_agent") as build_agent:
            mod._get()
        _, kwargs = build_agent.call_args
        self.assertFalse(kwargs["fast"])

    def test_reviewer_editor_defaults_to_guarded_even_unbound(self):
        import agents.reviewer_editor as mod

        mod._agent = None
        with patch("agents.reviewer_editor.build_agent") as build_agent:
            mod._get()
        _, kwargs = build_agent.call_args
        self.assertTrue(kwargs["guarded"], "ADR-0015 decision 7: Reviewer & Editor must default to guarded")

    def test_writer_defaults_to_guarded_even_unbound(self):
        import agents.writer as mod

        mod._agent = None
        with patch("agents.writer.build_agent") as build_agent:
            mod._get()
        _, kwargs = build_agent.call_args
        self.assertTrue(kwargs["guarded"])


if __name__ == "__main__":
    unittest.main()
