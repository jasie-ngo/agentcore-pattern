#!/usr/bin/env python3
"""CI/CD gate (ADR-0015 decision 7): fail the build if a fabric config YAML is
structurally invalid, or a member-facing-writer agent lacks a guardrail."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "hesta-claimsagent"))

from fabric.loader import load_fabric_config  # noqa: E402
from fabric.schema import FabricConfigError  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_fabric_config.py <path-to-workflow.yaml>", file=sys.stderr)
        return 2
    path = argv[1]
    try:
        load_fabric_config(path)
    except FabricConfigError as exc:
        print(f"INVALID fabric config: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — surface YAML/IO errors too
        print(f"ERROR reading '{path}': {exc}", file=sys.stderr)
        return 1
    print(f"OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
