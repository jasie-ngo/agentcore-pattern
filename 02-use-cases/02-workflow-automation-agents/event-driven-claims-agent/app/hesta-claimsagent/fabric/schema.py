"""Config schema for the agent fabric (ADR-0015 decision 1).

Two top-level blocks: ``agents:`` (named, reusable agent definitions) and
``workflow:`` (a node/edge graph referencing agents or deterministic callables by
name). This module defines the in-memory shape and structural/compliance
validation only — see loader.py for YAML parsing and registry.py for binding
these names to real Python callables.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class FabricConfigError(ValueError):
    """Raised for any structurally or semantically invalid fabric config."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    fast: bool = False
    guarded: bool = False
    role: str | None = None
    memory: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: str  # "agent" | "deterministic"
    implementation: str


@dataclass(frozen=True)
class EdgeSpec:
    source: str
    target: str
    router: str | None = None


@dataclass(frozen=True)
class WorkflowSpec:
    start: str
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]


@dataclass(frozen=True)
class FabricConfig:
    agents: dict[str, AgentSpec]
    workflow: WorkflowSpec


# Role tags whose agents MUST have a guardrail attached (ADR-0015 decision 7).
GUARDRAILED_ROLES = frozenset({"member_facing_writer"})

# Identity-based backstop: agents declared under these names are member-facing by
# definition and MUST be guarded, whether or not the config remembers to self-tag
# ``role: member_facing_writer``. ADR-0015 decision 7 chose "enforcement over
# injection" precisely so compliance can't be silently dropped by an incomplete
# client config — the role tag alone is opt-in and therefore not sufficient.
MEMBER_FACING_AGENTS = frozenset({"writer", "reviewer_editor"})


def validate_fabric_config(config: FabricConfig) -> None:
    """Raise FabricConfigError on any structural or compliance violation."""
    node_ids = [n.id for n in config.workflow.nodes]
    if len(set(node_ids)) != len(node_ids):
        raise FabricConfigError("duplicate node id in workflow.nodes")
    node_id_set = set(node_ids)

    if config.workflow.start not in node_id_set:
        raise FabricConfigError(f"workflow.start '{config.workflow.start}' is not a declared node")

    for node in config.workflow.nodes:
        if node.type == "agent":
            if node.implementation not in config.agents:
                raise FabricConfigError(f"node '{node.id}' references unknown agent '{node.implementation}'")
        elif node.type != "deterministic":
            raise FabricConfigError(f"node '{node.id}' has unknown type '{node.type}' (want agent|deterministic)")

    for edge in config.workflow.edges:
        if edge.source not in node_id_set:
            raise FabricConfigError(f"edge source '{edge.source}' is not a declared node")
        if edge.target not in node_id_set:
            raise FabricConfigError(f"edge target '{edge.target}' is not a declared node")

    for name, agent in config.agents.items():
        if agent.role in GUARDRAILED_ROLES and not agent.guarded:
            raise FabricConfigError(
                f"agent '{name}' has role '{agent.role}' which requires guarded=true "
                "(ADR-0015 decision 7) but guarded is false"
            )
        if name in MEMBER_FACING_AGENTS and not agent.guarded:
            raise FabricConfigError(
                f"agent '{name}' is member-facing and requires guarded=true "
                "(ADR-0015 decision 7) but guarded is false"
            )
