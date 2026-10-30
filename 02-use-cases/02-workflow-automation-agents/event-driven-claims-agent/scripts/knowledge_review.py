#!/usr/bin/env python3
"""Approve a reviewed knowledge draft. The client's named owner approves, after our review; this records it.

    ./.venv/bin/python3 scripts/knowledge_review.py approve acme 20261030T0930Z \\
        --approver-name "Jo Citizen" --approver-role "Head of Member Services" --reviewed-by "Our Reviewer"

The approval record binds the owner's name, role and the date to a hash of the exact files. It is unsigned JSON: it records who was
said to approve, it does not prove it. Selecting a version for the image is the last step, so a failed approval changes nothing.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "04_hesta_member_email"))

from core import approval, knowledge_store  # noqa: E402


class ReviewError(Exception):
    """The draft cannot be approved as asked."""


def approve(
    client_dir: Path, draft_version: str, *, client: str, approved_by_name: str, approved_by_role: str, reviewed_by: str, now: str
) -> Path:
    base = Path(client_dir) / "knowledge"
    draft = base / "drafts" / draft_version
    if not draft.is_dir():
        raise ReviewError(f"no draft {draft_version} under {base / 'drafts'}")
    target = base / "approved" / draft_version
    if target.exists():
        raise ReviewError(f"{target} already exists: a version is approved once")
    knowledge_store.parse(draft, version=draft_version)  # re-validate: the draft may have been edited since it was written
    staging = target.with_name(target.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(draft, staging, ignore=shutil.ignore_patterns("REVIEW.md"))
    try:
        approval.write_approval(
            staging, client=client, version=draft_version, approved_by_name=approved_by_name,
            approved_by_role=approved_by_role, reviewed_by=reviewed_by, approved_at=now,
        )
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    (base / "approved" / "CURRENT").write_text(draft_version + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("approve")
    p.add_argument("client")
    p.add_argument("draft_version")
    p.add_argument("--approver-name", required=True)
    p.add_argument("--approver-role", required=True)
    p.add_argument("--reviewed-by", required=True)
    args = ap.parse_args(argv)
    try:
        folder = approve(
            ROOT / "clients" / args.client, args.draft_version, client=args.client, approved_by_name=args.approver_name,
            approved_by_role=args.approver_role, reviewed_by=args.reviewed_by,
            now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except (ReviewError, approval.ApprovalError, knowledge_store.KnowledgeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"approved: {folder}\nselected for the next build: approved/CURRENT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
