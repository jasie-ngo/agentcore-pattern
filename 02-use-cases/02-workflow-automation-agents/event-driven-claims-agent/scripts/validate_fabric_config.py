#!/usr/bin/env python3
"""CI/CD gate (ADR-0015 decision 7): fail the build if a fabric config YAML is
structurally invalid, a member-facing agent lacks a guardrail, or a node/router
name doesn't resolve to anything registered in fabric.adapters / fabric.routers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "hesta-claimsagent"))

from fabric import adapters, registry, routers  # noqa: E402,F401 — imports register node/router implementations
from fabric.loader import load_fabric_config  # noqa: E402
from fabric.schema import FabricConfig, FabricConfigError  # noqa: E402


def _unregistered_names(config: FabricConfig) -> list[str]:
    """Names referenced by the config that no decorator ever registered."""
    problems: list[str] = []
    for node in config.workflow.nodes:
        table = registry.AGENT_NODES if node.type == "agent" else registry.DETERMINISTIC_NODES
        if node.implementation not in table:
            problems.append(
                f"node '{node.id}' references unregistered {node.type} implementation "
                f"'{node.implementation}'"
            )
    for edge in config.workflow.edges:
        if edge.router and edge.router not in registry.ROUTERS:
            problems.append(
                f"edge '{edge.source}' -> '{edge.target}' references unregistered router '{edge.router}'"
            )
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_fabric_config.py <path-to-workflow.yaml>", file=sys.stderr)
        return 2
    path = argv[1]
    try:
        config = load_fabric_config(path)
    except FabricConfigError as exc:
        print(f"INVALID fabric config: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — surface YAML/IO errors too
        print(f"ERROR reading '{path}': {exc}", file=sys.stderr)
        return 1

    problems = _unregistered_names(config)
    if problems:
        for problem in problems:
            print(f"INVALID fabric config: {problem}", file=sys.stderr)
        return 1

    print(f"OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
