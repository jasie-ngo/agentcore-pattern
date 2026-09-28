#!/usr/bin/env python3
"""Seed HESTA member records from the real sample-email fixtures.

The seed shape matches ``Hesta-members``: ``member_id`` is the partition key and
the lookup Lambda can also query the ``email`` GSI. The same normalizer used by
the runtime extracts identifiers and sender addresses from both fixture shapes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app" / "hesta-claimsagent"))

from ingestion.email_normalizer import normalize_email  # noqa: E402

SAMPLE_DIR = PROJECT_ROOT / "hesta" / "sample-emails"
OMITTED_FIXTURES = {
    "ADVICE_60080001_which_option_is_best.txt",
    "BDBN_60010005_remove_beneficiaries_nominate_estate.txt",
}
MEMBER_ID_RE = re.compile(r"^[^_]+_([0-9]{5,9})_")
FROM_NAME_RE = re.compile(r"^From:\s*(.*?)\s*<[^>]+>", re.IGNORECASE | re.MULTILINE)
FORM_NAME_RE = re.compile(r"(?:^|\n)name\s*:\s*(.+?)(?=\n\w[\w-]*\s*:|\Z)", re.IGNORECASE | re.DOTALL)


def _display_name(raw: str) -> str:
    form_match = FORM_NAME_RE.search(raw)
    if form_match:
        return " ".join(form_match.group(1).split())
    from_match = FROM_NAME_RE.search(raw)
    if from_match:
        return " ".join(from_match.group(1).split())
    return "Unknown member"


def _member_id(path: Path, inbound) -> str | None:
    if inbound.member_number_for_lookup:
        return inbound.member_number_for_lookup
    match = MEMBER_ID_RE.match(path.name)
    return match.group(1) if match else None


def build_members() -> list[dict]:
    records: dict[str, dict] = {}
    for path in sorted(SAMPLE_DIR.glob("*.txt")):
        if path.name in OMITTED_FIXTURES:
            continue
        raw = path.read_text(encoding="utf-8")
        inbound = normalize_email(raw)
        member_id = _member_id(path, inbound)
        if not member_id or not inbound.from_email:
            raise ValueError(f"Could not extract member_id/email from {path.name}")
        scenario = path.name.split("_", 1)[0]
        records.setdefault(
            member_id,
            {
                "member_id": member_id,
                "email": inbound.from_email,
                "name": _display_name(raw),
                "status": "active",
                "scenario": scenario,
                "source_file": path.name,
            },
        )
    return list(records.values())


MEMBERS = build_members()


def get_table_name(region: str, stack_name: str = "AgentCore-ClaimsAgentV2-dev") -> str:
    """Resolve the new stack's Members table without touching the old stack."""
    import boto3

    cfn = boto3.client("cloudformation", region_name=region)
    response = cfn.list_stack_resources(StackName=stack_name)
    for resource in response.get("StackResourceSummaries", []):
        if resource["ResourceType"] == "AWS::DynamoDB::Table" and "MembersTable" in resource["LogicalResourceId"]:
            return resource["PhysicalResourceId"]
    raise RuntimeError(f"MembersTable was not found in {stack_name}")


def seed(region: str, dry_run: bool) -> None:
    print(f"Seeding {len(MEMBERS)} unique HESTA members from 41 fixtures")
    print(f"Omitted fixtures: {', '.join(sorted(OMITTED_FIXTURES))}")
    if dry_run:
        for member in MEMBERS:
            print(member)
        print("dry-run: no AWS writes")
        return

    import boto3

    table_name = get_table_name(region)
    print(f"Writing to {table_name} in {region}")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    with table.batch_writer() as batch:
        for member in MEMBERS:
            batch.put_item(Item=member)
    print(f"Wrote {len(MEMBERS)} records")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the ClaimsAgentV2 HESTA members table.")
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--dry-run", action="store_true", help="Print derived records without AWS writes.")
    args = parser.parse_args()
    seed(args.region, args.dry_run)
