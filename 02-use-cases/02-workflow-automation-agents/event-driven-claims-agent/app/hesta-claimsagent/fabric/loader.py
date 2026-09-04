"""YAML loading for the agent fabric config (ADR-0015 decision 1)."""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import (
    AgentSpec,
    EdgeSpec,
    FabricConfig,
    FabricConfigError,
    NodeSpec,
    WorkflowSpec,
    validate_fabric_config,
)


def _parse_agents(raw: dict | None) -> dict[str, AgentSpec]:
    agents: dict[str, AgentSpec] = {}
    for name, spec in (raw or {}).items():
        if not isinstance(spec, dict):
            raise FabricConfigError(f"agents.{name} must be a mapping")
        agents[name] = AgentSpec(
            name=name,
            fast=bool(spec.get("fast", False)),
            guarded=bool(spec.get("guarded", False)),
            role=spec.get("role"),
            memory=tuple(spec.get("memory", ())),
        )
    return agents


def _parse_workflow(raw: dict | None) -> WorkflowSpec:
    if not raw or "start" not in raw:
        raise FabricConfigError("workflow.start is required")
    nodes = tuple(
        NodeSpec(id=n["id"], type=n["type"], implementation=n["implementation"]) for n in raw.get("nodes", [])
    )
    edges = tuple(
        EdgeSpec(source=e["source"], target=e["target"], router=e.get("router")) for e in raw.get("edges", [])
    )
    return WorkflowSpec(start=raw["start"], nodes=nodes, edges=edges)


def load_fabric_config(path: str | Path) -> FabricConfig:
    """Load, parse, and validate a fabric config YAML file.

    Raises FabricConfigError (or a yaml/IO error) on any parsing/validation failure —
    never returns a partially-valid config.
    """
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise FabricConfigError(f"{path}: top-level YAML must be a mapping")
    config = FabricConfig(
        agents=_parse_agents(raw.get("agents")),
        workflow=_parse_workflow(raw.get("workflow")),
    )
    validate_fabric_config(config)
    return config
