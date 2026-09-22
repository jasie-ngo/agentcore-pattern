"""Unit tests for the current HESTA Gateway Lambda handlers.

DynamoDB calls are mocked, so these tests do not access AWS.
"""

import importlib.util
import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
_ROOT = os.path.join(os.path.dirname(__file__), "..")

try:
    import boto3
    from botocore.exceptions import ClientError

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False


def _load(module_name: str, rel_path: str):
    path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class MemberLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("member_lookup_handler", "lambdas/member_lookup/handler.py")

    def setUp(self):
        self.original_table = self.mod.table
        self.mod.table = MagicMock()

    def tearDown(self):
        self.mod.table = self.original_table

    def test_requires_member_id_or_email(self):
        result = self.mod.handler({}, None)
        self.assertEqual(result, {"error": "member_id or email required"})
        self.mod.table.get_item.assert_not_called()
        self.mod.table.query.assert_not_called()

    def test_lookup_by_member_id(self):
        item = {"member_id": "60010001", "email": "sarah@example.com", "status": "active"}
        self.mod.table.get_item.return_value = {"Item": item}
        result = self.mod.handler({"member_id": "60010001"}, None)
        self.assertEqual(result, item)
        self.mod.table.get_item.assert_called_once_with(Key={"member_id": "60010001"})

    def test_lookup_by_email(self):
        item = {"member_id": "60010001", "email": "sarah@example.com"}
        self.mod.table.query.return_value = {"Items": [item]}
        result = self.mod.handler({"email": item["email"]}, None)
        self.assertEqual(result, item)
        self.mod.table.query.assert_called_once()

    def test_email_not_found(self):
        self.mod.table.query.return_value = {"Items": []}
        result = self.mod.handler({"email": "missing@example.com"}, None)
        self.assertEqual(result, {"error": "Member not found"})


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class CaseLookupCreationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("case_lookup_creation_handler", "lambdas/case_lookup_creation/handler.py")

    def setUp(self):
        self.original_table = self.mod.table
        self.mod.table = MagicMock()

    def tearDown(self):
        self.mod.table = self.original_table

    def test_requires_member_id(self):
        """No verified member_id -> routed away from the case path, nothing created."""
        result = self.mod.handler({"sender_email": "unknown@example.com"}, None)
        self.assertEqual(result, {"error": "verified member_id required; case lookup skipped"})
        self.mod.table.query.assert_not_called()
        self.mod.table.put_item.assert_not_called()

    def test_creates_verified_case_with_inbound_history(self):
        self.mod.table.query.return_value = {"Items": []}
        result = self.mod.handler(
            {
                "member_id": "60010001",
                "primary_intent_id": "death_benefit_nomination",
                "inbound_email": "Please send my BDBN form.",
            },
            None,
        )
        self.assertEqual(result["status"], "new_case_created")
        self.assertTrue(result["case"]["case_id"].startswith("CASE-"))
        self.assertEqual(result["case"]["member_id"], "60010001")
        self.assertEqual(result["case"]["identity_status"], "verified")
        # primary_intent_id is still recorded for audit, but no longer gates reuse.
        self.assertEqual(result["case"]["primary_intent_id"], "death_benefit_nomination")
        history = result["conversation_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["entry_type"], "inbound_member_email")
        self.assertEqual(history[0]["content"], "Please send my BDBN form.")
        self.assertEqual(history[0]["intent_id"], "death_benefit_nomination")
        # No attachments_present sent -> defaults to 0, not omitted (0 is meaningful, not absent).
        self.assertEqual(history[0]["attachments_present"], 0)
        self.mod.table.put_item.assert_called_once()

    def test_first_message_attachment_is_recorded_on_the_case_creation_entry(self):
        """Regression: a brand-new case's very first history entry is written here at case
        creation, not via the later append call — main.py's later copy of this same entry_id
        is silently deduped, so if attachments_present isn't captured HERE, it's lost forever
        and a follow-up message incorrectly re-requests an attachment already on file."""
        self.mod.table.query.return_value = {"Items": []}
        result = self.mod.handler(
            {
                "member_id": "60010001",
                "primary_intent_id": "death_benefit_nomination",
                "inbound_email": "Please find attached my BDBN form.",
                "attachments_present": 1,
            },
            None,
        )
        self.assertEqual(result["conversation_history"][0]["attachments_present"], 1)

    def test_validated_attachment_status_is_recorded_on_the_case_creation_entry(self):
        """TODO 6: the same ordering problem as above, now for the validated status/form_id/
        missing_fields (not just the raw count) — main.py's early, empty-history assessment
        seeds these on a brand-new case's bootstrap entry."""
        self.mod.table.query.return_value = {"Items": []}
        result = self.mod.handler(
            {
                "member_id": "60010001",
                "primary_intent_id": "death_benefit_nomination",
                "inbound_email": "Please find attached my BDBN form, mostly filled in.",
                "attachments_present": 1,
                "attachment_status": "incomplete",
                "form_id": "BDBN-NOM-V1",
                "missing_fields": ["Nomination details"],
            },
            None,
        )
        entry = result["conversation_history"][0]
        self.assertEqual(entry["attachment_status"], "incomplete")
        self.assertEqual(entry["form_id"], "BDBN-NOM-V1")
        self.assertEqual(entry["missing_fields"], ["Nomination details"])

    def test_absent_attachment_status_is_not_written(self):
        """No attachment_status/form_id/missing_fields sent (e.g. legacy caller) → not written
        at all, rather than as None/empty — keeps old rows and new rows the same shape."""
        self.mod.table.query.return_value = {"Items": []}
        result = self.mod.handler(
            {"member_id": "60010001", "primary_intent_id": "death_benefit_nomination", "inbound_email": "hi"},
            None,
        )
        entry = result["conversation_history"][0]
        self.assertNotIn("attachment_status", entry)
        self.assertNotIn("form_id", entry)
        self.assertNotIn("missing_fields", entry)

    def test_reuses_the_members_case_regardless_of_intent(self):
        """One case per member: a different topic on a follow-up still reuses it."""
        existing = {
            "case_id": "CASE-EXIST",
            "member_id": "60010001",
            "status": "Open",
            "primary_intent_id": "death_benefit_nomination",
            "created_at": "2026-09-01T00:00:00+00:00",
            "idempotency_key": "old-key",
            "conversation_history": [{"entry_id": "old-key:inbound", "entry_type": "inbound_member_email"}],
        }
        self.mod.table.query.return_value = {"Items": [existing]}
        result = self.mod.handler(
            {"member_id": "60010001", "primary_intent_id": "beneficiary_update", "inbound_email": "different topic"},
            None,
        )
        self.assertEqual(result["status"], "existing_cases_found")
        self.assertEqual(result["case_id"], "CASE-EXIST")
        self.assertEqual(result["cases"], [existing])
        self.assertEqual(result["conversation_history"], existing["conversation_history"])
        self.mod.table.put_item.assert_not_called()

    def test_reuses_closed_case_instead_of_creating_new(self):
        """A reply to a closed case reuses that same case (Writer is told it's closed)."""
        existing = {
            "case_id": "CASE-EXIST",
            "member_id": "60010001",
            "status": "closed",
            "primary_intent_id": "death_benefit_nomination",
            "created_at": "2026-09-01T00:00:00+00:00",
            "idempotency_key": "old-key",
        }
        self.mod.table.query.return_value = {"Items": [existing]}
        result = self.mod.handler(
            {"member_id": "60010001", "primary_intent_id": "death_benefit_nomination", "inbound_email": "what happened?"},
            None,
        )
        self.assertEqual(result["status"], "existing_cases_found")
        self.assertEqual(result["case_id"], "CASE-EXIST")
        self.mod.table.put_item.assert_not_called()

    def test_multiple_legacy_cases_pick_earliest_deterministically(self):
        """Legacy data with >1 case per member (pre-dating this rule) picks one, deterministically."""
        older = {
            "case_id": "CASE-OLDER",
            "member_id": "60010001",
            "status": "Open",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        newer = {
            "case_id": "CASE-NEWER",
            "member_id": "60010001",
            "status": "Open",
            "created_at": "2026-06-01T00:00:00+00:00",
        }
        self.mod.table.query.return_value = {"Items": [newer, older]}
        result = self.mod.handler({"member_id": "60010001", "inbound_email": "hi"}, None)
        self.assertEqual(result["case_id"], "CASE-OLDER")

    def test_duplicate_trigger_delivery_reuses_same_case(self):
        """Replaying the same idempotency_key (e.g. a re-delivered trigger event) is a no-op."""
        existing = {
            "case_id": "CASE-REPLAY",
            "member_id": "60010001",
            "status": "closed",
            "primary_intent_id": "beneficiary_update",
            "created_at": "2026-09-01T00:00:00+00:00",
            "idempotency_key": "fixed-idempotency-key",
        }
        self.mod.table.query.return_value = {"Items": [existing]}
        result = self.mod.handler(
            {
                "member_id": "60010001",
                "primary_intent_id": "death_benefit_nomination",
                "idempotency_key": "fixed-idempotency-key",
            },
            None,
        )
        self.assertEqual(result["status"], "existing_cases_found")
        self.assertEqual(result["case_id"], "CASE-REPLAY")
        self.mod.table.put_item.assert_not_called()

    def test_concurrent_creation_falls_back_to_existing_case(self):
        """A conditional-write race on put_item resolves to the winner's case, not a duplicate."""
        self.mod.table.query.return_value = {"Items": []}
        expected_case_id = self.mod._case_id("60010001")
        winner = {"case_id": expected_case_id, "member_id": "60010001", "status": "Open"}
        self.mod.table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem"
        )
        self.mod.table.get_item.return_value = {"Item": winner}
        result = self.mod.handler(
            {
                "member_id": "60010001",
                "primary_intent_id": "death_benefit_nomination",
                "idempotency_key": "fixed-key",
            },
            None,
        )
        self.assertEqual(result["status"], "existing_cases_found")
        self.assertEqual(result["case_id"], expected_case_id)


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class CaseHistoryAppendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("case_lookup_creation_history_handler", "lambdas/case_lookup_creation/handler.py")

    def setUp(self):
        self.original_table = self.mod.table
        self.mod.table = MagicMock()

    def tearDown(self):
        self.mod.table = self.original_table

    def test_case_not_found(self):
        self.mod.table.get_item.return_value = {}
        result = self.mod.handler(
            {"case_id": "CASE-MISSING", "history_entries": [{"entry_id": "a"}]}, None
        )
        self.assertEqual(result, {"error": "Case not found"})

    def test_appends_new_entries(self):
        self.mod.table.get_item.return_value = {
            "Item": {"case_id": "CASE-1", "conversation_history": [], "history_revision": 0}
        }
        self.mod.table.update_item.return_value = {
            "Attributes": {
                "case_id": "CASE-1",
                "conversation_history": [{"entry_id": "k:draft", "entry_type": "generated_draft"}],
            }
        }
        result = self.mod.handler(
            {
                "case_id": "CASE-1",
                "history_entries": [{"entry_id": "k:draft", "entry_type": "generated_draft", "content": "hi"}],
            },
            None,
        )
        self.assertEqual(result["status"], "history_appended")
        self.mod.table.update_item.assert_called_once()
        kwargs = self.mod.table.update_item.call_args.kwargs
        self.assertEqual(kwargs["ExpressionAttributeValues"][":next_revision"], 1)

    def test_reprocessing_same_entry_id_does_not_duplicate(self):
        self.mod.table.get_item.return_value = {
            "Item": {
                "case_id": "CASE-1",
                "conversation_history": [{"entry_id": "k:inbound", "entry_type": "inbound_member_email"}],
                "history_revision": 1,
            }
        }
        result = self.mod.handler(
            {"case_id": "CASE-1", "history_entries": [{"entry_id": "k:inbound", "entry_type": "inbound_member_email"}]},
            None,
        )
        self.assertEqual(result["status"], "history_unchanged")
        self.mod.table.update_item.assert_not_called()

    def test_history_stays_bounded(self):
        existing_history = [{"entry_id": f"old-{i}", "entry_type": "generated_draft"} for i in range(30)]
        self.mod.table.get_item.return_value = {
            "Item": {"case_id": "CASE-1", "conversation_history": existing_history, "history_revision": 5}
        }
        self.mod.table.update_item.return_value = {"Attributes": {}}
        self.mod.handler(
            {"case_id": "CASE-1", "history_entries": [{"entry_id": "new-1", "entry_type": "generated_draft"}]},
            None,
        )
        kwargs = self.mod.table.update_item.call_args.kwargs
        written_history = kwargs["ExpressionAttributeValues"][":history"]
        self.assertEqual(len(written_history), self.mod._MAX_HISTORY_ENTRIES)
        self.assertEqual(written_history[-1]["entry_id"], "new-1")

    def test_appended_entry_content_is_capped(self):
        """Draft/review entries (and a reused case's inbound entry) only ever reach DynamoDB
        via this append path — the byte cap must be enforced here, not only at case creation."""
        self.mod.table.get_item.return_value = {
            "Item": {"case_id": "CASE-1", "conversation_history": [], "history_revision": 0}
        }
        self.mod.table.update_item.return_value = {"Attributes": {}}
        oversized = "x" * (self.mod._MAX_HISTORY_ENTRY_BYTES + 500)
        self.mod.handler(
            {
                "case_id": "CASE-1",
                "history_entries": [{"entry_id": "k:draft", "entry_type": "generated_draft", "content": oversized}],
            },
            None,
        )
        kwargs = self.mod.table.update_item.call_args.kwargs
        written_history = kwargs["ExpressionAttributeValues"][":history"]
        self.assertEqual(len(written_history[0]["content"]), self.mod._MAX_HISTORY_ENTRY_BYTES)


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class EmailReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("email_review_handler", "lambdas/email_review/handler.py")

    def setUp(self):
        self.original_table = self.mod.table
        self.mod.table = MagicMock()

    def tearDown(self):
        self.mod.table = self.original_table

    def test_requires_case_id(self):
        result = self.mod.handler({}, None)
        self.assertEqual(result, {"error": "case_id required"})
        self.mod.table.put_item.assert_not_called()

    def test_writes_review_record(self):
        event = {
            "case_id": "CASE-1234",
            "draft_subject": "HESTA enquiry",
            "draft_body": "Please provide your member number.",
            "escalation_reasons": "identity not verified",
        }
        result = self.mod.handler(event, None)
        self.assertTrue(result["success"])
        self.assertEqual(result["case_id"], "CASE-1234")
        self.assertTrue(result["review_id"])
        written = self.mod.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(written["case_id"], "CASE-1234")
        self.assertEqual(written["status"], "pending_review")
        self.assertEqual(written["escalation_reasons"], "identity not verified")
        self.assertEqual(written["revision_count"], 0)
        self.assertNotIn("attachment_status", written)
        self.assertNotIn("review_result", written)

    def test_writes_attachment_and_review_metadata(self):
        event = {
            "case_id": "CASE-1234",
            "draft_subject": "HESTA enquiry",
            "draft_body": "Please provide your member number.",
            "escalation_reasons": "draft not approved after 2 revision(s)",
            "revision_count": 2,
            "attachment_status": "missing",
            "attachment_notes": "Expected a Binding Death Nomination form, but none was detected.",
            "review_result": {
                "approved_for_human_send": False,
                "accuracy_ok": True,
                "tone_ok": True,
                "compliance_ok": False,
                "edits": "",
                "issues": ["compliance issue"],
            },
        }
        self.mod.handler(event, None)
        written = self.mod.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(written["revision_count"], 2)
        self.assertEqual(written["attachment_status"], "missing")
        self.assertEqual(written["review_result"]["approved_for_human_send"], False)
        self.assertEqual(written["review_result"]["issues"], ["compliance issue"])

    def test_writes_form_and_field_metadata(self):
        """TODO 6: form_id/form_name/missing_fields/received_filenames let a reviewer see
        exactly what arrived and what's blank, without reading application logs."""
        event = {
            "case_id": "CASE-1234",
            "draft_subject": "HESTA enquiry",
            "draft_body": "Please provide the missing field.",
            "escalation_reasons": "draft not approved",
            "attachment_status": "incomplete",
            "form_id": "BDBN-NOM-V1",
            "form_name": "Binding Death Benefit Nomination Form",
            "missing_fields": ["Nomination details"],
            "received_filenames": ["bdbn.txt"],
        }
        self.mod.handler(event, None)
        written = self.mod.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(written["form_id"], "BDBN-NOM-V1")
        self.assertEqual(written["form_name"], "Binding Death Benefit Nomination Form")
        self.assertEqual(written["missing_fields"], ["Nomination details"])
        self.assertEqual(written["received_filenames"], ["bdbn.txt"])

    def test_no_attachment_bytes_are_persisted(self):
        """Rule: metadata only — never the attachment's file contents."""
        event = {
            "case_id": "CASE-1234",
            "draft_subject": "s",
            "draft_body": "b",
            "escalation_reasons": "x",
            "attachment_status": "valid",
        }
        self.mod.handler(event, None)
        written = self.mod.table.put_item.call_args.kwargs["Item"]
        self.assertNotIn("attachment_text", written)
        self.assertNotIn("attachment_content", written)


if __name__ == "__main__":
    unittest.main()
