"""Structural + compliance validation for the fabric config schema (ADR-0015 decisions 1, 7)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from fabric.schema import (  # noqa: E402
    AgentSpec,
    EdgeSpec,
    FabricConfig,
    FabricConfigError,
    NodeSpec,
    WorkflowSpec,
    validate_fabric_config,
)


def _minimal_config(name: str = "drafter", **agent_overrides) -> FabricConfig:
    agent = AgentSpec(name=name, **agent_overrides)
    node = NodeSpec(id=name, type="agent", implementation=name)
    workflow = WorkflowSpec(start=name, nodes=(node,), edges=())
    return FabricConfig(agents={name: agent}, workflow=workflow)


class ValidateFabricConfigTests(unittest.TestCase):
    def test_minimal_valid_config_passes(self):
        validate_fabric_config(_minimal_config())  # must not raise

    def test_unknown_start_node_raises(self):
        node = NodeSpec(id="a", type="deterministic", implementation="a")
        workflow = WorkflowSpec(start="missing", nodes=(node,), edges=())
        with self.assertRaises(FabricConfigError):
            validate_fabric_config(FabricConfig(agents={}, workflow=workflow))

    def test_duplicate_node_id_raises(self):
        nodes = (
            NodeSpec(id="a", type="deterministic", implementation="a"),
            NodeSpec(id="a", type="deterministic", implementation="a2"),
        )
        workflow = WorkflowSpec(start="a", nodes=nodes, edges=())
        with self.assertRaises(FabricConfigError):
            validate_fabric_config(FabricConfig(agents={}, workflow=workflow))

    def test_agent_node_referencing_unknown_agent_raises(self):
        node = NodeSpec(id="a", type="agent", implementation="does_not_exist")
        workflow = WorkflowSpec(start="a", nodes=(node,), edges=())
        with self.assertRaises(FabricConfigError):
            validate_fabric_config(FabricConfig(agents={}, workflow=workflow))

    def test_unknown_node_type_raises(self):
        node = NodeSpec(id="a", type="not_a_type", implementation="a")
        workflow = WorkflowSpec(start="a", nodes=(node,), edges=())
        with self.assertRaises(FabricConfigError):
            validate_fabric_config(FabricConfig(agents={}, workflow=workflow))

    def test_edge_with_unknown_source_raises(self):
        node = NodeSpec(id="a", type="deterministic", implementation="a")
        edge = EdgeSpec(source="missing", target="a")
        workflow = WorkflowSpec(start="a", nodes=(node,), edges=(edge,))
        with self.assertRaises(FabricConfigError):
            validate_fabric_config(FabricConfig(agents={}, workflow=workflow))

    def test_edge_with_unknown_target_raises(self):
        node = NodeSpec(id="a", type="deterministic", implementation="a")
        edge = EdgeSpec(source="a", target="missing")
        workflow = WorkflowSpec(start="a", nodes=(node,), edges=(edge,))
        with self.assertRaises(FabricConfigError):
            validate_fabric_config(FabricConfig(agents={}, workflow=workflow))

    def test_member_facing_role_without_guardrail_raises(self):
        with self.assertRaises(FabricConfigError):
            validate_fabric_config(_minimal_config(role="member_facing_writer", guarded=False))

    def test_member_facing_role_with_guardrail_passes(self):
        validate_fabric_config(_minimal_config(role="member_facing_writer", guarded=True))  # must not raise

    def test_member_facing_agent_name_without_role_or_guardrail_raises(self):
        """Identity backstop: a config that never self-tags ``role`` must still be
        rejected for the known member-facing agent names (ADR-0015 decision 7)."""
        for name in ("writer", "reviewer_editor"):
            with self.subTest(name=name), self.assertRaises(FabricConfigError):
                validate_fabric_config(_minimal_config(name=name, guarded=False))

    def test_member_facing_agent_name_with_guardrail_passes(self):
        for name in ("writer", "reviewer_editor"):
            with self.subTest(name=name):
                validate_fabric_config(_minimal_config(name=name, guarded=True))  # must not raise


if __name__ == "__main__":
    unittest.main()
