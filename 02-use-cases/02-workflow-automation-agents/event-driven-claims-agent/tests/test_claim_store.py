"""core/claim_store.py: the record, optimistic locking, and the Gateway-backed store against a fake tool.

Run:  ./.venv/bin/python3 -m unittest tests.test_claim_store -v
"""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core import claim_store as cs  # noqa: E402


def record(ref="CLR-1A2B3C4D", **kw):
    base = dict(
        claim_ref=ref, state="awaiting_staff", version=0, sender_email="a@example.com", message_ids=("<m1@x>",),
        pending_draft={"subject": f"[{ref}] Re: x", "body": "Hi"},
        history=({"at": "2026-10-30T00:00:00Z", "event": "inbound_processed", "from": "open", "to": "awaiting_staff"},),
        last_activity="2026-10-30T00:00:00Z",
    )
    base.update(kw)
    return cs.ClaimRecord(**base)


class RecordTests(unittest.TestCase):
    def test_item_round_trip(self):
        r = record()
        self.assertEqual(cs.ClaimRecord.from_item(r.to_item()), r)

    def test_round_trip_without_a_draft(self):
        r = record(pending_draft=None)
        self.assertIsNone(cs.ClaimRecord.from_item(r.to_item()).pending_draft)

    def test_item_has_no_status_attribute(self):
        # list_pending_claims keys on a top-level `status`; a lifecycle record must not appear in that sparse index.
        self.assertNotIn("status", record().to_item())

    def test_item_marks_itself_as_a_lifecycle_record(self):
        self.assertEqual(record().to_item()["kind"], "lifecycle")


class StoreContract:
    """Behaviour every store must have."""

    def make(self):
        raise NotImplementedError

    def test_create_then_get(self):
        store = self.make()
        saved = store.save(record(), 0)
        self.assertEqual(saved.version, 1)
        self.assertEqual(store.get("CLR-1A2B3C4D").version, 1)

    def test_get_missing_is_none(self):
        self.assertIsNone(self.make().get("CLR-NOPE0000"))

    def test_create_twice_conflicts(self):
        store = self.make()
        store.save(record(), 0)
        with self.assertRaises(cs.ConflictError):
            store.save(record(), 0)

    def test_stale_version_conflicts(self):
        store = self.make()
        first = store.save(record(), 0)
        store.save(replace(first, state="resolved"), first.version)
        with self.assertRaises(cs.ConflictError):
            store.save(replace(first, state="closed"), first.version)

    def test_message_ids_are_indexed(self):
        store = self.make()
        store.save(record(), 0)
        self.assertEqual(store.ref_of_message_id("<m1@x>"), "CLR-1A2B3C4D")
        self.assertIsNone(store.ref_of_message_id("<unknown@x>"))

    def test_new_message_ids_are_indexed_on_update(self):
        store = self.make()
        first = store.save(record(), 0)
        store.save(replace(first, message_ids=first.message_ids + ("<m2@x>",)), first.version)
        self.assertEqual(store.ref_of_message_id("<m2@x>"), "CLR-1A2B3C4D")


class InMemoryTests(StoreContract, unittest.TestCase):
    def make(self):
        return cs.InMemoryClaimStore()


class FakeTool:
    """Stands in for the claim_state Gateway tool: same contract as lambdas/claim_state/handler.py."""

    def __init__(self):
        self.items, self.alias = {}, {}

    def __call__(self, name, args):
        assert name == "claim_state"
        action = args["action"]
        if action == "get":
            return {"item": self.items.get(args["claim_ref"])}
        if action == "by_message":
            return {"claim_ref": self.alias.get(args["message_id"])}
        if action == "put":
            item, expected = args["item"], args["expected_version"]
            current = self.items.get(item["claim_id"])
            if (current["version"] if current else 0) != expected:
                return {"error": "conflict"}
            item = {**item, "version": expected + 1}
            self.items[item["claim_id"]] = item
            for mid in item["message_ids"]:
                self.alias[mid] = item["claim_id"]
            return {"item": item}
        raise AssertionError(action)


class GatewayStoreTests(StoreContract, unittest.TestCase):
    def make(self):
        return cs.GatewayClaimStore(FakeTool())

    def test_an_error_from_the_tool_that_is_not_a_conflict_raises(self):
        store = cs.GatewayClaimStore(lambda name, args: {"error": "table unavailable"})
        with self.assertRaisesRegex(RuntimeError, "table unavailable"):
            store.get("CLR-1A2B3C4D")


if __name__ == "__main__":
    unittest.main()
