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

    def test_a_missing_required_key_is_malformed(self):
        for key in ("claim_id", "lifecycle_state", "version", "sender_email", "last_activity"):
            with self.subTest(key=key):
                item = record().to_item()
                del item[key]
                with self.assertRaisesRegex(ValueError, "malformed claim item"):
                    cs.ClaimRecord.from_item(item)

    def test_a_non_string_pending_draft_is_malformed(self):
        item = record().to_item()
        item["pending_draft"] = {"subject": "x"}
        with self.assertRaisesRegex(ValueError, "malformed claim item"):
            cs.ClaimRecord.from_item(item)

    def test_a_non_string_history_is_malformed(self):
        item = record().to_item()
        item["history"] = [{"event": "x"}]
        with self.assertRaisesRegex(ValueError, "malformed claim item"):
            cs.ClaimRecord.from_item(item)

    def test_message_ids_must_be_a_list_of_strings(self):
        for bad in ("<m1@x>", [1], [None]):
            with self.subTest(bad=bad):
                item = record().to_item()
                item["message_ids"] = bad
                with self.assertRaisesRegex(ValueError, "malformed claim item"):
                    cs.ClaimRecord.from_item(item)

    def test_json_that_does_not_parse_is_malformed(self):
        for key, bad in (("pending_draft", "{not json"), ("history", "[")):
            with self.subTest(key=key):
                item = record().to_item()
                item[key] = bad
                with self.assertRaisesRegex(ValueError, "malformed claim item"):
                    cs.ClaimRecord.from_item(item)

    def test_a_version_that_is_not_an_integer_is_malformed(self):
        item = record().to_item()
        item["version"] = "abc"
        with self.assertRaisesRegex(ValueError, "malformed claim item"):
            cs.ClaimRecord.from_item(item)


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

    def test_a_message_id_owned_by_another_claim_cannot_be_stolen(self):
        store = self.make()
        store.save(record("CLR-AAAAAAAA", message_ids=("<shared@x>",)), 0)
        with self.assertRaises(cs.ConflictError):
            store.save(record("CLR-BBBBBBBB", message_ids=("<shared@x>",)), 0)
        self.assertEqual(store.ref_of_message_id("<shared@x>"), "CLR-AAAAAAAA")
        self.assertIsNone(store.get("CLR-BBBBBBBB"))

    def test_a_claim_can_update_itself_with_the_same_message_ids(self):
        store = self.make()
        first = store.save(record(), 0)
        second = store.save(replace(first, state="resolved"), first.version)
        self.assertEqual(second.version, 2)
        self.assertEqual(store.ref_of_message_id("<m1@x>"), "CLR-1A2B3C4D")

    def test_two_different_new_claims_with_different_ids_both_work(self):
        store = self.make()
        store.save(record("CLR-AAAAAAAA", message_ids=("<a@x>",)), 0)
        store.save(record("CLR-BBBBBBBB", message_ids=("<b@x>",)), 0)
        self.assertEqual(store.ref_of_message_id("<a@x>"), "CLR-AAAAAAAA")
        self.assertEqual(store.ref_of_message_id("<b@x>"), "CLR-BBBBBBBB")


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
            owners = [self.alias.get(mid) for mid in item["message_ids"]]
            if any(owner is not None and owner != item["claim_id"] for owner in owners):
                return {"error": "conflict"}
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

    def test_a_reply_that_is_not_a_dict_is_malformed_for_every_action(self):
        store = cs.GatewayClaimStore(lambda name, args: None)
        with self.assertRaisesRegex(RuntimeError, "claim_state returned a malformed reply"):
            store.get("CLR-1A2B3C4D")
        with self.assertRaisesRegex(RuntimeError, "claim_state returned a malformed reply"):
            store.ref_of_message_id("<m1@x>")
        with self.assertRaisesRegex(RuntimeError, "claim_state returned a malformed reply"):
            store.save(record(), 0)

    def test_a_put_reply_without_an_item_is_malformed(self):
        store = cs.GatewayClaimStore(lambda name, args: {})
        with self.assertRaisesRegex(RuntimeError, "claim_state returned a malformed reply"):
            store.save(record(), 0)

    def test_a_put_reply_whose_item_is_not_a_dict_is_malformed(self):
        store = cs.GatewayClaimStore(lambda name, args: {"item": "x"})
        with self.assertRaisesRegex(RuntimeError, "claim_state returned a malformed reply"):
            store.save(record(), 0)

    def test_a_put_reply_with_the_wrong_version_is_refused(self):
        item = {**record().to_item(), "version": 5}
        store = cs.GatewayClaimStore(lambda name, args: {"item": item})
        with self.assertRaisesRegex(RuntimeError, "version 5, expected 1"):
            store.save(record(), 0)

    def test_a_get_item_missing_a_key_propagates_as_malformed(self):
        store = cs.GatewayClaimStore(lambda name, args: {"item": {"claim_id": "CLR-1A2B3C4D"}})
        with self.assertRaisesRegex(ValueError, "malformed claim item"):
            store.get("CLR-1A2B3C4D")


if __name__ == "__main__":
    unittest.main()
