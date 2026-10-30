"""The claim record and where it lives.

Claim STATUS lives here (the graph owns it); facts about the member live in AgentCore Memory. The record is one item in the existing
claims table, keyed by claim reference, with a version number for optimistic locking. It carries no top-level ``status``, so it never
shows up in list_pending_claims' status index. Message ids are indexed as small alias items so the mail headers can find the claim.

Two stores share one contract: an in-memory one for tests, and a Gateway-backed one that calls the ``claim_state`` tool through a
caller-supplied function (the Gateway client is built on the request thread, see ADR-0017).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Callable


class ConflictError(Exception):
    """Another writer changed the claim first (or created it)."""


_REQUIRED_KEYS = ("claim_id", "lifecycle_state", "version", "sender_email", "last_activity")
_MALFORMED_REPLY = "claim_state returned a malformed reply"


def _json_field(raw, key: str, kind: type):
    """Decode a JSON-text attribute; empty text means 'absent' (None for a dict, [] for a list)."""
    if not isinstance(raw, str):
        raise ValueError(f"malformed claim item: {key} is not a string")
    if not raw:
        return None if kind is dict else []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed claim item: {key} is not valid JSON") from exc
    if not isinstance(value, kind):
        raise ValueError(f"malformed claim item: {key} is not a JSON {kind.__name__}")
    return value


@dataclass(frozen=True)
class ClaimRecord:
    claim_ref: str
    state: str
    version: int
    sender_email: str
    message_ids: tuple
    pending_draft: dict | None
    history: tuple
    last_activity: str

    def to_item(self) -> dict:
        return {
            "claim_id": self.claim_ref,
            "kind": "lifecycle",
            "lifecycle_state": self.state,
            "version": self.version,
            "sender_email": self.sender_email,
            "message_ids": list(self.message_ids),
            "pending_draft": json.dumps(self.pending_draft) if self.pending_draft is not None else "",
            "history": json.dumps(list(self.history)),
            "last_activity": self.last_activity,
        }

    @classmethod
    def from_item(cls, item: dict) -> "ClaimRecord":
        if not isinstance(item, dict):
            raise ValueError("malformed claim item: not a mapping")
        missing = [key for key in _REQUIRED_KEYS if key not in item]
        if missing:
            raise ValueError(f"malformed claim item: missing {', '.join(missing)}")
        try:
            version = int(item["version"])
        except (TypeError, ValueError) as exc:
            raise ValueError("malformed claim item: version is not an integer") from exc
        message_ids = item.get("message_ids", ())
        if not isinstance(message_ids, (list, tuple)) or not all(isinstance(mid, str) for mid in message_ids):
            raise ValueError("malformed claim item: message_ids is not a list of strings")
        draft = _json_field(item.get("pending_draft", ""), "pending_draft", dict)
        history = _json_field(item.get("history", "") or "[]", "history", list)
        return cls(
            claim_ref=item["claim_id"],
            state=item["lifecycle_state"],
            version=version,
            sender_email=item["sender_email"],
            message_ids=tuple(message_ids),
            pending_draft=draft,
            history=tuple(history),
            last_activity=item["last_activity"],
        )


class InMemoryClaimStore:
    def __init__(self):
        self._records: dict[str, ClaimRecord] = {}
        self._alias: dict[str, str] = {}

    def get(self, ref: str) -> ClaimRecord | None:
        return self._records.get(ref)

    def ref_of_message_id(self, mid: str) -> str | None:
        return self._alias.get(mid)

    def save(self, record: ClaimRecord, expected_version: int) -> ClaimRecord:
        current = self._records.get(record.claim_ref)
        if (current.version if current else 0) != expected_version:
            raise ConflictError(f"{record.claim_ref} is at version {current.version if current else 0}, expected {expected_version}")
        for mid in record.message_ids:
            owner = self._alias.get(mid)
            if owner is not None and owner != record.claim_ref:
                raise ConflictError(f"message id {mid} already belongs to {owner}")
        stored = replace(record, version=expected_version + 1)
        self._records[record.claim_ref] = stored
        for mid in stored.message_ids:
            self._alias[mid] = stored.claim_ref
        return stored


class GatewayClaimStore:
    def __init__(self, call: Callable[[str, dict], dict]):
        self._call = call

    def _ask(self, args: dict) -> dict:
        out = self._call("claim_state", args)
        if not isinstance(out, dict):
            raise RuntimeError(_MALFORMED_REPLY)
        if "error" in out and out["error"] != "conflict":
            raise RuntimeError(f"claim_state failed: {out['error']}")
        return out

    def get(self, ref: str) -> ClaimRecord | None:
        item = self._ask({"action": "get", "claim_ref": ref}).get("item")
        return ClaimRecord.from_item(item) if item else None

    def ref_of_message_id(self, mid: str) -> str | None:
        return self._ask({"action": "by_message", "message_id": mid}).get("claim_ref")

    def save(self, record: ClaimRecord, expected_version: int) -> ClaimRecord:
        out = self._ask({"action": "put", "item": record.to_item(), "expected_version": expected_version})
        if out.get("error") == "conflict":
            raise ConflictError(f"{record.claim_ref}: expected version {expected_version}")
        item = out.get("item")
        if not isinstance(item, dict):
            raise RuntimeError(_MALFORMED_REPLY)
        stored = ClaimRecord.from_item(item)
        if stored.version != expected_version + 1:
            raise RuntimeError(f"claim_state returned version {stored.version}, expected {expected_version + 1} for {record.claim_ref}")
        return stored
