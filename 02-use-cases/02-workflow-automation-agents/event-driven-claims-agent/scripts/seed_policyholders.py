#!/usr/bin/env python3
"""Seed ONE client's own Policies and Claims tables from that client's synthetic demo data.

    python3 scripts/seed_policyholders.py --client fininsurer --list                 # print what would be seeded (no AWS)
    python3 scripts/seed_policyholders.py --client fininsurer --dry-run              # show the table names and counts (no AWS)
    python3 scripts/seed_policyholders.py --client fininsurer --region ap-southeast-2
    python3 scripts/seed_policyholders.py --client fininsurer --reset --region ap-southeast-2   # remove earlier demo records first

The data is clients/<client>/demo/policyholders.json (or --data). The tables are the existing Policies table (partition key
``policy_number``) and Claims table (``claim_id``): the schema is unchanged, only item attributes are added, so ``lookup_policy`` and
``list_pending_claims`` keep working.

This script is deliberately strict, because a pre-sale demo must hold synthetic data only and nothing in the code can tell real data from
invented data by itself:

* it needs ``--client`` (or AGENTCORE_CLIENT) and never touches the shared, original tables;
* every record must be marked ``"synthetic": true`` and every email must be on a reserved example domain
  (example.com, example.net, example.org), or it refuses to run;
* ``--reset`` deletes only records marked synthetic, never the whole table.

For a client the table name comes from its own stack, and the fallback is its exact name: the fuzzy "any table containing Policies" scan
the original seed scripts use is never applied here, because in a shared account it would find another client's table.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DOMAINS = ("example.com", "example.net", "example.org")


class SeedError(Exception):
    """A readable reason the data cannot be seeded."""


def data_path(client: str, root: Path = ROOT) -> Path:
    return root / "clients" / client / "demo" / "policyholders.json"


def load_data(path: Path) -> dict:
    """Read and check the data file. Raises SeedError listing every problem, so one run shows them all."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SeedError(f"{path} cannot be read: {exc}") from exc
    except ValueError as exc:
        raise SeedError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise SeedError(f'{path} must be a JSON object with "version": 1')

    problems: list[str] = []
    holders = raw.get("policyholders")
    claims = raw.get("claims", [])
    if not isinstance(holders, list) or not holders:
        raise SeedError(f'{path} needs a non-empty "policyholders" list')
    if not isinstance(claims, list):
        raise SeedError(f'{path}: "claims" must be a list')

    numbers: set[str] = set()
    for i, rec in enumerate(holders):
        where = f"policyholders[{i}]"
        if not isinstance(rec, dict):
            problems.append(f"{where} must be an object")
            continue
        number = rec.get("policy_number")
        if not number or not isinstance(number, str):
            problems.append(f"{where} has no policy_number")
        elif number in numbers:
            problems.append(f"{where} repeats policy_number {number}")
        else:
            numbers.add(number)
        if rec.get("synthetic") is not True:
            problems.append(f'{where} ({number}) is not marked "synthetic": true')
        domain = str(rec.get("email", "")).rsplit("@", 1)[-1].lower()
        if domain not in EXAMPLE_DOMAINS:
            problems.append(f"{where} ({number}) has email {rec.get('email')!r}: a demo may use only {', '.join(EXAMPLE_DOMAINS)}")
        for field in ("holder_name", "email", "policy_type", "status"):
            if not rec.get(field):
                problems.append(f"{where} ({number}) has no {field}")

    claim_ids: set[str] = set()
    for i, rec in enumerate(claims):
        where = f"claims[{i}]"
        if not isinstance(rec, dict):
            problems.append(f"{where} must be an object")
            continue
        cid = rec.get("claim_id")
        if not cid or not isinstance(cid, str):
            problems.append(f"{where} has no claim_id")
        elif cid in claim_ids:
            problems.append(f"{where} repeats claim_id {cid}")
        else:
            claim_ids.add(cid)
        if rec.get("synthetic") is not True:
            problems.append(f'{where} ({cid}) is not marked "synthetic": true')
        if rec.get("policy_number") not in numbers:
            problems.append(f"{where} ({cid}) refers to policy {rec.get('policy_number')!r}, which is not in policyholders")

    if problems:
        raise SeedError(f"{path} cannot be seeded:\n" + "\n".join(f" - {p}" for p in problems))
    return raw


def table_name(region: str, ctx, kind: str) -> str:
    """This client's own table: found from its stack, else its exact name. Never a fuzzy scan."""
    import boto3  # noqa: PLC0415 - only needed when talking to AWS

    try:
        cfn = boto3.client("cloudformation", region_name=region)
        for r in cfn.list_stack_resources(StackName=ctx.stack_name).get("StackResourceSummaries", []):
            if r["ResourceType"] == "AWS::DynamoDB::Table" and kind in r["LogicalResourceId"]:
                return r["PhysicalResourceId"]
    except Exception:  # noqa: BLE001 - a missing stack falls back to the exact name below
        pass
    return ctx.table(kind)


def reset(table, key: str) -> int:
    """Delete only the records marked synthetic. Returns how many."""
    from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

    removed = 0
    kwargs = {"FilterExpression": Attr("synthetic").eq(True), "ProjectionExpression": key}
    while True:
        page = table.scan(**kwargs)
        with table.batch_writer() as batch:
            for item in page.get("Items", []):
                batch.delete_item(Key={key: item[key]})
                removed += 1
        if "LastEvaluatedKey" not in page:
            return removed
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def seed(region: str, ctx, data: dict, *, dry_run: bool, do_reset: bool) -> None:
    holders, claims = data["policyholders"], data.get("claims", [])
    if dry_run:
        # Offline: the exact names for this client. A deployed stack may resolve a name differently, which a real run reports.
        print(f"Would seed {len(holders)} policyholders into {ctx.table('Policies')} and {len(claims)} claims into "
              f"{ctx.table('Claims')} ({region}){' after a reset of earlier synthetic records' if do_reset else ''}")
        print("   (dry-run: no AWS calls, no writes)")
        return
    import boto3  # noqa: PLC0415

    policies_name = table_name(region, ctx, "Policies")
    claims_name = table_name(region, ctx, "Claims") if claims or do_reset else ""
    print(f"Seeding {len(holders)} policyholders into {policies_name} and {len(claims)} claims into {claims_name or '(none)'} ({region})")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    policies = dynamodb.Table(policies_name)
    if do_reset:
        print(f"   reset: removed {reset(policies, 'policy_number')} earlier synthetic policyholders")
        print(f"   reset: removed {reset(dynamodb.Table(claims_name), 'claim_id')} earlier synthetic claims")
    with policies.batch_writer() as batch:
        for rec in holders:
            batch.put_item(Item=rec)
    if claims:
        with dynamodb.Table(claims_name).batch_writer() as batch:
            for rec in claims:
                batch.put_item(Item=rec)
    print(f"Wrote {len(holders)} policyholders and {len(claims)} claims.")


def print_reference(data: dict) -> None:
    """A markdown reference of what would be seeded (no AWS)."""
    print("# Synthetic policyholders (DynamoDB seed reference)\n")
    print("Send each sample email **from the policyholder's email** and quote the **policy number** so the identity check can verify it.\n")
    print("| Policy # | Name | Email | Type | Status | Days in arrears | Scenario |")
    print("|---|---|---|---|---|---|---|")
    for r in data["policyholders"]:
        print(f"| {r['policy_number']} | {r['holder_name']} | {r['email']} | {r['policy_type']} | {r['status']} | "
              f"{r.get('days_in_arrears', 0)} | {r.get('test_scenario', '')} |")
    if data.get("claims"):
        print("\n## Pending claims\n")
        print("| Claim | Policy # | Category | Status | Description |")
        print("|---|---|---|---|---|")
        for c in data["claims"]:
            print(f"| {c['claim_id']} | {c['policy_number']} | {c.get('category', '')} | {c.get('status', '')} | {c.get('description', '')} |")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a client's own tables from its synthetic demo data.")
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--data", type=Path, help="the data file (default: clients/<client>/demo/policyholders.json)")
    parser.add_argument("--dry-run", action="store_true", help="show the table names and counts; no AWS calls, no writes")
    parser.add_argument("--list", action="store_true", help="print a markdown reference of the data (no AWS)")
    parser.add_argument("--reset", action="store_true", help="first remove earlier records marked synthetic")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import clientctx  # noqa: PLC0415

    clientctx.add_arguments(parser)
    args = parser.parse_args(argv)

    client = (args.client or os.environ.get("AGENTCORE_CLIENT", "")).strip()
    if not client:
        print("ERROR: --client is required (or AGENTCORE_CLIENT). This script never writes to the shared original tables.", file=sys.stderr)
        return 2
    try:
        data = load_data(args.data or data_path(client))
        if args.list:
            print_reference(data)
            return 0
        ctx = clientctx.context_from_args(args)
        if not ctx.client:
            raise SeedError("no client resolved; refusing to touch the original tables")
        seed(args.region, ctx, data, dry_run=args.dry_run, do_reset=args.reset)
    except (SeedError, clientctx.CtxError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
