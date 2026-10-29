#!/usr/bin/env python3
"""Seed the tables a use case declares in gateway_tools.yaml with its test data.

Generic: works for any use case with created tables (``tables:`` entries
without ``existing:``) and an ``app/<use_case>/seed_data.yaml`` keyed by table:

    customers:
      - { customer_id: CUST-100001, name: Mia Chen, ... }

Table names follow the CDK rule in infra-construct.ts:
``<stack>-<use_case>-<table key>`` with ``_`` → ``-``.

    uv run --project app python scripts/seed_tables.py card_dispute_agent --dry-run
    uv run --project app python scripts/seed_tables.py card_dispute_agent --region ap-southeast-2
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "agentcore" / "generated" / "manifest.json"


def table_name(stack: str, use_case: str, key: str) -> str:
    """Physical table name — must match infra-construct.ts."""
    return f"{stack}-{use_case}-{key}".replace("_", "-")


def plan(use_case: str, stack: str) -> list[tuple[str, str, list[dict]]]:
    """Return [(table key, physical name, items)] and validate the seed data."""
    seed_path = ROOT / "app" / use_case / "seed_data.yaml"
    if not seed_path.is_file():
        raise SystemExit(f"No seed data: {seed_path.relative_to(ROOT)}")
    seed = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
    created = {
        t["key"]: t for t in json.loads(MANIFEST.read_text(encoding="utf-8"))["tables"] if t["use_case"] == use_case
    }
    out = []
    for key, items in seed.items():
        if key not in created:
            raise SystemExit(
                f"seed_data.yaml: '{key}' is not a table created for {use_case} "
                f"(created: {sorted(created) or 'none'}) — declare it in gateway_tools.yaml and rebuild"
            )
        pk = created[key]["partition_key"]["name"]
        missing = [i for i, item in enumerate(items) if pk not in item]
        if missing:
            raise SystemExit(f"seed_data.yaml: {key} items {missing} have no '{pk}'")
        out.append((key, table_name(stack, use_case, key), items))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("use_case", help="Use case folder under app/, e.g. card_dispute_agent")
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--stack-name", default="AgentCore-ClaimsAgent-dev")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written; no AWS calls.")
    args = parser.parse_args()

    tables = plan(args.use_case, args.stack_name)
    if args.dry_run:
        for key, name, items in tables:
            print(f"{key:18s} -> {name}: {len(items)} item(s)")
        print("dry-run: no AWS writes")
        return 0

    import boto3

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    for key, name, items in tables:
        with dynamodb.Table(name).batch_writer() as batch:
            for item in items:
                batch.put_item(Item=json.loads(json.dumps(item), parse_float=Decimal))
        print(f"{key:18s} -> {name}: wrote {len(items)} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
