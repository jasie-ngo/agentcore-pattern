"""Approval records for client-owned content that is baked into an image.

A client's named owner approves a folder of files. The record stores a hash of every file in the folder, so an approval is bound to
the exact content: change one byte and the approval no longer holds. The record is plain JSON and unsigned (the same known
limitation as rule-pack approvals), so it proves the content is the content that was recorded, not who recorded it.

Client-neutral: the client's name is a parameter.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

APPROVAL_FILE = "approval.json"
_FIELDS = ("client", "version", "content_sha256", "approved_by_name", "approved_by_role", "approved_at", "reviewed_by")


class ApprovalError(Exception):
    """The content is not approved, or no longer matches its approval."""


def digest_dir(root: Path) -> str:
    """sha256 over each file's relative path and bytes, in sorted order, excluding only the top-level approval record."""
    digest = hashlib.sha256()
    record = Path(root) / APPROVAL_FILE
    files = sorted(p for p in Path(root).rglob("*") if p.is_file() and p != record)
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_approval(
    root: Path, *, client: str, version: str, approved_by_name: str, approved_by_role: str, reviewed_by: str, approved_at: str
) -> dict:
    record = {
        "client": client,
        "version": version,
        "content_sha256": digest_dir(root),
        "approved_by_name": approved_by_name,
        "approved_by_role": approved_by_role,
        "approved_at": approved_at,
        "reviewed_by": reviewed_by,
    }
    for field in _FIELDS:
        if not str(record[field]).strip():
            raise ApprovalError(f"{field} must not be blank")
    (Path(root) / APPROVAL_FILE).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def verify_approval(root: Path, *, client: str) -> dict:
    path = Path(root) / APPROVAL_FILE
    if not path.is_file():
        raise ApprovalError(f"{root} has no approval.json: the content is not approved")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ApprovalError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise ApprovalError(f"{path} must hold a JSON object")
    for field in _FIELDS:
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ApprovalError(f"{path} is missing {field}")
    if record["client"] != client:
        raise ApprovalError(f'{path} belongs to "{record["client"]}", not "{client}"')
    if record["content_sha256"] != digest_dir(root):
        raise ApprovalError(f"{root} has changed since approval (hash mismatch): re-review and re-approve it")
    return record
