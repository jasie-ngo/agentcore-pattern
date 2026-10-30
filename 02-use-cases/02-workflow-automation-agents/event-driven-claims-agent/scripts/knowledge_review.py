#!/usr/bin/env python3
"""Approve a reviewed knowledge draft. The client's named owner approves, after our review; this records it.

    ./.venv/bin/python3 scripts/knowledge_review.py approve acme 20261030T0930Z \\
        --approver-name "Jo Citizen" --approver-role "Head of Member Services" --reviewed-by "Our Reviewer"

The approval record binds the owner's name, role and the date to a hash of the exact files. It is unsigned JSON: it records who was
said to approve, it does not prove it. Selecting a version for the image is the last step, so a failed approval changes nothing.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "04_hesta_member_email"))

from core import approval, knowledge_store  # noqa: E402

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_RESERVED = "CURRENT"
_STAGING_SUFFIX = ".staging"


class ReviewError(Exception):
    """The draft cannot be approved as asked."""


def _check_client(client: str) -> None:
    if not isinstance(client, str) or not _NAME.fullmatch(client):
        raise ReviewError(f"client name {client!r} is not allowed: use letters, digits, '.', '_' or '-'")


def _check_version(version: str, base: Path) -> None:
    if not isinstance(version, str) or not _NAME.fullmatch(version):
        raise ReviewError(f"draft version {version!r} is not allowed: use letters, digits, '.', '_' or '-'")
    if version == _RESERVED or version.endswith(_STAGING_SUFFIX):
        raise ReviewError(f"draft version {version!r} is reserved")
    for path in (base / "drafts" / version, base / "approved" / version):
        if not path.resolve().is_relative_to(base.resolve()):
            raise ReviewError(f"{path} is outside {base}")


def _current(base: Path) -> str | None:
    path = base / "approved" / _RESERVED
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def _select(base: Path, version: str) -> None:
    """Point CURRENT at version in one step: write a temporary file, then replace. A failure leaves the old CURRENT in place."""
    tmp = base / "approved" / (_RESERVED + ".tmp")
    tmp.write_text(version + "\n", encoding="utf-8")
    try:
        os.replace(tmp, base / "approved" / _RESERVED)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def approve(
    client_dir: Path, draft_version: str, *, client: str, approved_by_name: str, approved_by_role: str, reviewed_by: str, now: str
) -> Path:
    _check_client(client)
    base = Path(client_dir) / "knowledge"
    _check_version(draft_version, base)
    draft = base / "drafts" / draft_version
    if not draft.is_dir():
        raise ReviewError(f"no draft {draft_version} under {base / 'drafts'}")
    if draft.is_symlink() or any(p.is_symlink() for p in draft.rglob("*")):
        raise ReviewError(f"draft {draft_version} contains a symlink: refused")
    target = base / "approved" / draft_version
    if target.exists():
        # approved but never selected (a previous run failed during selection): finish the job for this client's own record
        if _current(base) == draft_version:
            raise ReviewError(f"{target} already exists: a version is approved once")
        approval.verify_approval(target, client=client)
        _select(base, draft_version)
        return target
    knowledge_store.parse(draft, version=draft_version)  # re-validate: the draft may have been edited since it was written
    staging = target.with_name(target.name + _STAGING_SUFFIX)
    if staging.exists():
        shutil.rmtree(staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(draft, staging, symlinks=True, ignore=shutil.ignore_patterns("REVIEW.md"))
        knowledge_store.parse(staging, version=draft_version)  # validate the copy the record will bind, not just the source
        approval.write_approval(
            staging, client=client, version=draft_version, approved_by_name=approved_by_name,
            approved_by_role=approved_by_role, reviewed_by=reviewed_by, approved_at=now,
        )
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _select(base, draft_version)
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
    except (ReviewError, approval.ApprovalError, knowledge_store.KnowledgeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"approved: {folder}\nselected for the next build: approved/CURRENT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
