"""fabric.registry: agent-spec resolution + node/router registration (ADR-0015 decision 1)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from fabric import registry  # noqa: E402
from fabric.schema import AgentSpec, FabricConfig, NodeSpec, WorkflowSpec  # noqa: E402


class RegistryTests(unittest.TestCase):
    def setUp(self):
        registry.reset()

    def tearDown(self):
        registry.reset()
        registry.DETERMINISTIC_NODES.pop("t_det", None)
        registry.AGENT_NODES.pop("t_agent", None)
        registry.ROUTERS.pop("t_router", None)

    def test_spec_for_returns_default_when_unbound(self):
        spec = registry.spec_for("intent_identifier", default_fast=True)
        self.assertEqual(spec, AgentSpec(name="intent_identifier", fast=True))

    def test_spec_for_returns_bound_spec_when_declared(self):
        configured = AgentSpec(name="writer", fast=False, guarded=True, role="member_facing_writer")
        node = NodeSpec(id="writer", type="agent", implementation="writer")
        workflow = WorkflowSpec(start="writer", nodes=(node,), edges=())
        registry.bind(FabricConfig(agents={"writer": configured}, workflow=workflow))

        self.assertEqual(registry.spec_for("writer"), configured)

    def test_default_guarded_is_a_floor_the_config_cannot_lower(self):
        """ADR-0015 decision 7: enforcement over injection — a config that declares
        an agent must not be able to silently drop the caller's guardrail default."""
        configured = AgentSpec(name="writer", fast=False, guarded=False)
        node = NodeSpec(id="writer", type="agent", implementation="writer")
        workflow = WorkflowSpec(start="writer", nodes=(node,), edges=())
        registry.bind(FabricConfig(agents={"writer": configured}, workflow=workflow))

        spec = registry.spec_for("writer", default_guarded=True)
        self.assertTrue(spec.guarded, "config must not be able to lower guarded below the caller's floor")
        self.assertEqual(spec.name, "writer")

    def test_config_can_raise_guarded_above_the_default(self):
        configured = AgentSpec(name="empathy", guarded=True)
        node = NodeSpec(id="empathy", type="agent", implementation="empathy")
        workflow = WorkflowSpec(start="empathy", nodes=(node,), edges=())
        registry.bind(FabricConfig(agents={"empathy": configured}, workflow=workflow))

        self.assertTrue(registry.spec_for("empathy", default_guarded=False).guarded)

    def test_config_still_wins_for_fast_which_has_no_compliance_floor(self):
        configured = AgentSpec(name="empathy", fast=False)
        node = NodeSpec(id="empathy", type="agent", implementation="empathy")
        workflow = WorkflowSpec(start="empathy", nodes=(node,), edges=())
        registry.bind(FabricConfig(agents={"empathy": configured}, workflow=workflow))

        self.assertFalse(registry.spec_for("empathy", default_fast=True).fast)

    def test_spec_for_falls_back_for_undeclared_agent_even_when_bound(self):
        node = NodeSpec(id="writer", type="agent", implementation="writer")
        workflow = WorkflowSpec(start="writer", nodes=(node,), edges=())
        registry.bind(FabricConfig(agents={}, workflow=workflow))

        spec = registry.spec_for("empathy", default_fast=True)
        self.assertEqual(spec, AgentSpec(name="empathy", fast=True))

    def test_reset_clears_binding(self):
        node = NodeSpec(id="a", type="agent", implementation="a")
        workflow = WorkflowSpec(start="a", nodes=(node,), edges=())
        registry.bind(FabricConfig(agents={"a": AgentSpec(name="a", guarded=True)}, workflow=workflow))
        registry.reset()
        self.assertFalse(registry.spec_for("a").guarded)

    def test_deterministic_node_decorator_registers_and_returns_fn(self):
        @registry.deterministic_node("t_det")
        def _fn(state):
            return state

        self.assertIs(registry.DETERMINISTIC_NODES["t_det"], _fn)

    def test_agent_node_decorator_registers_and_returns_fn(self):
        @registry.agent_node("t_agent")
        def _fn(state):
            return state

        self.assertIs(registry.AGENT_NODES["t_agent"], _fn)

    def test_router_decorator_registers_and_returns_fn(self):
        @registry.router("t_router")
        def _fn(state):
            return True

        self.assertIs(registry.ROUTERS["t_router"], _fn)


if __name__ == "__main__":
    unittest.main()
