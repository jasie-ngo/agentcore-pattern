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
    """TODO 2: a case is keyed on (member_id, thread_key) — a member can have several
    concurrent cases, one per email thread. No member_id-index query, no history on the item."""

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
        result = self.mod.handler({"sender_email": "unknown@example.com", "thread_key": "x"}, None)
        self.assertEqual(result, {"error": "verified member_id required; case lookup skipped"})
        self.mod.table.get_item.assert_not_called()
        self.mod.table.put_item.assert_not_called()

    def test_requires_thread_key(self):
        result = self.mod.handler({"member_id": "60010001"}, None)
        self.assertEqual(result, {"error": "thread_key required; case lookup skipped"})
        self.mod.table.get_item.assert_not_called()
        self.mod.table.put_item.assert_not_called()

    def test_creates_verified_case_with_no_email_text_on_the_item(self):
        self.mod.table.get_item.return_value = {}
        result = self.mod.handler(
            {
                "member_id": "60010001",
                "thread_key": "binding nomination",
                "subject": "Binding nomination",
                "primary_intent_id": "death_benefit_nomination",
            },
            None,
        )
        self.assertEqual(result["status"], "new_case_created")
        case = result["case"]
        self.assertTrue(case["case_id"].startswith("CASE-"))
        self.assertEqual(case["member_id"], "60010001")
        self.assertEqual(case["thread_key"], "binding nomination")
        self.assertEqual(case["subject"], "Binding nomination")
        self.assertEqual(case["identity_status"], "verified")
        self.assertEqual(case["primary_intent_id"], "death_benefit_nomination")
        self.assertEqual(case["last_intent_id"], "death_benefit_nomination")
        self.assertEqual(case["forms_on_file"], {})
        self.assertNotIn("conversation_history", case)
        self.mod.table.put_item.assert_called_once()

    def test_same_member_same_thread_reuses_the_same_case(self):
        """'X' then 'RE: X' normalize to the same thread_key -> the same case_id."""
        existing = {"case_id": self.mod._case_id("60010001", "binding nomination"), "member_id": "60010001"}
        self.mod.table.get_item.return_value = {"Item": existing}
        result = self.mod.handler(
            {"member_id": "60010001", "thread_key": "binding nomination", "primary_intent_id": "death_benefit_nomination"},
            None,
        )
        self.assertEqual(result["status"], "existing_case_found")
        self.assertEqual(result["case_id"], existing["case_id"])
        self.assertEqual(result["case"], existing)
        self.mod.table.put_item.assert_not_called()

    def test_same_member_different_thread_is_a_different_case(self):
        id_a = self.mod._case_id("60010001", "binding nomination")
        id_b = self.mod._case_id("60010001", "change of address")
        self.assertNotEqual(id_a, id_b)

    def test_different_member_same_subject_is_a_different_case(self):
        id_a = self.mod._case_id("60010001", "binding nomination")
        id_b = self.mod._case_id("60010002", "binding nomination")
        self.assertNotEqual(id_a, id_b)

    def test_concurrent_creation_falls_back_to_existing_case(self):
        """A conditional-write race on put_item resolves to the winner's case, not a duplicate."""
        self.mod.table.get_item.side_effect = [
            {},  # initial lookup: not found
            {"Item": {"case_id": self.mod._case_id("60010001", "x"), "member_id": "60010001"}},  # re-read after race
        ]
        self.mod.table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem"
        )
        result = self.mod.handler(
            {"member_id": "60010001", "thread_key": "x", "primary_intent_id": "death_benefit_nomination"},
            None,
        )
        self.assertEqual(result["status"], "existing_case_found")
        self.assertEqual(result["case_id"], self.mod._case_id("60010001", "x"))


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class CaseFactsUpdateTests(unittest.TestCase):
    """TODO 2.5 / TODO 4.3: case_id + case_facts sets last_intent_id/last_contact_at, marks
    'valid' forms_on_file entries via an unconditional per-key SET (idempotent, no prior read),
    and tags any other status 'received_not_valid' via its OWN small conditional update that
    can never downgrade a form already recorded 'valid'."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load("case_lookup_creation_facts_handler", "lambdas/case_lookup_creation/handler.py")

    def setUp(self):
        self.original_table = self.mod.table
        self.mod.table = MagicMock()

    def tearDown(self):
        self.mod.table = self.original_table

    def test_case_not_found(self):
        self.mod.table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "missing"}}, "UpdateItem"
        )
        result = self.mod.handler(
            {"case_id": "CASE-MISSING", "case_facts": {"last_intent_id": "death_benefit_nomination"}}, None
        )
        self.assertEqual(result, {"error": "Case not found"})

    def test_no_prior_read_is_performed(self):
        """Race-free by design: never a get_item first, for either write."""
        self.mod.table.update_item.return_value = {"Attributes": {"case_id": "CASE-1"}}
        self.mod.handler(
            {
                "case_id": "CASE-1",
                "case_facts": {"last_intent_id": "beneficiary_update", "forms_on_file": {"BDBN-NOM-V1": "incomplete"}},
            },
            None,
        )
        self.mod.table.get_item.assert_not_called()

    def test_sets_last_intent_id_and_last_contact_at(self):
        self.mod.table.update_item.return_value = {"Attributes": {"case_id": "CASE-1"}}
        result = self.mod.handler(
            {"case_id": "CASE-1", "case_facts": {"last_intent_id": "beneficiary_update"}}, None
        )
        self.assertEqual(result["status"], "facts_updated")
        kwargs = self.mod.table.update_item.call_args.kwargs
        self.assertEqual(kwargs["ExpressionAttributeValues"][":last_intent_id"], "beneficiary_update")
        self.assertIn(":now", kwargs["ExpressionAttributeValues"])
        self.assertEqual(kwargs["ConditionExpression"], "attribute_exists(case_id)")
        self.mod.table.update_item.assert_called_once()

    def test_marks_a_form_valid_via_a_nested_set_not_a_whole_map_overwrite(self):
        self.mod.table.update_item.return_value = {"Attributes": {}}
        self.mod.handler(
            {
                "case_id": "CASE-1",
                "case_facts": {"last_intent_id": "death_benefit_nomination", "forms_on_file": {"BDBN-NOM-V1": "valid"}},
            },
            None,
        )
        kwargs = self.mod.table.update_item.call_args.kwargs
        self.assertIn("forms_on_file.#f0 = :f0", kwargs["UpdateExpression"])
        self.assertEqual(kwargs["ExpressionAttributeNames"]["#f0"], "BDBN-NOM-V1")
        self.assertEqual(kwargs["ExpressionAttributeValues"][":f0"], "valid")
        self.mod.table.update_item.assert_called_once()  # a 'valid' entry needs no second call

    def test_a_non_valid_status_is_tagged_received_not_valid_not_coerced_to_valid(self):
        """Regression: this route must never claim a form is 'valid' just because SOME status
        was sent — only an actually-valid assessment may ever write 'valid'."""
        self.mod.table.update_item.return_value = {"Attributes": {}}
        self.mod.handler(
            {"case_id": "CASE-1", "case_facts": {"forms_on_file": {"BDBN-NOM-V1": "incomplete"}}}, None
        )
        # Primary call: no form fields at all (only 'valid' forms go in it).
        primary_kwargs = self.mod.table.update_item.call_args_list[0].kwargs
        self.assertNotIn("forms_on_file", primary_kwargs["UpdateExpression"])
        # Secondary call: the specific conditional write that tags it.
        secondary_kwargs = self.mod.table.update_item.call_args_list[1].kwargs
        self.assertEqual(secondary_kwargs["UpdateExpression"], "SET forms_on_file.#fid = :status")
        self.assertEqual(secondary_kwargs["ExpressionAttributeNames"]["#fid"], "BDBN-NOM-V1")
        self.assertEqual(secondary_kwargs["ExpressionAttributeValues"][":status"], "received_not_valid")
        self.assertEqual(
            secondary_kwargs["ConditionExpression"],
            "attribute_not_exists(forms_on_file.#fid) OR forms_on_file.#fid <> :valid",
        )

    def test_a_valid_form_is_not_reverted_by_a_later_incomplete(self):
        """The conditional write for the non-valid tag is refused (ConditionalCheckFailedException)
        when the form is already 'valid' — the handler must treat that as success, not an error,
        and the primary update (last_intent_id etc.) must still have gone through."""
        self.mod.table.update_item.side_effect = [
            {"Attributes": {"case_id": "CASE-1", "forms_on_file": {"BDBN-NOM-V1": "valid"}}},
            ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "already valid"}}, "UpdateItem"),
        ]
        result = self.mod.handler(
            {
                "case_id": "CASE-1",
                "case_facts": {"last_intent_id": "death_benefit_nomination", "forms_on_file": {"BDBN-NOM-V1": "incomplete"}},
            },
            None,
        )
        self.assertEqual(result["status"], "facts_updated")
        self.assertEqual(self.mod.table.update_item.call_count, 2)

    def test_multiple_forms_get_distinct_placeholders(self):
        self.mod.table.update_item.return_value = {"Attributes": {}}
        self.mod.handler(
            {
                "case_id": "CASE-1",
                "case_facts": {"forms_on_file": {"BDBN-NOM-V1": "valid", "COD-V1": "valid"}},
            },
            None,
        )
        kwargs = self.mod.table.update_item.call_args.kwargs
        self.assertEqual(set(kwargs["ExpressionAttributeNames"].values()), {"BDBN-NOM-V1", "COD-V1"})
        self.assertTrue(all(v == "valid" for k, v in kwargs["ExpressionAttributeValues"].items() if k != ":now"))

    def test_mixed_valid_and_non_valid_forms_in_one_call(self):
        self.mod.table.update_item.return_value = {"Attributes": {}}
        self.mod.handler(
            {
                "case_id": "CASE-1",
                "case_facts": {"forms_on_file": {"BDBN-NOM-V1": "valid", "COD-V1": "incomplete"}},
            },
            None,
        )
        self.assertEqual(self.mod.table.update_item.call_count, 2)
        primary_kwargs = self.mod.table.update_item.call_args_list[0].kwargs
        self.assertEqual(primary_kwargs["ExpressionAttributeNames"]["#f0"], "BDBN-NOM-V1")
        secondary_kwargs = self.mod.table.update_item.call_args_list[1].kwargs
        self.assertEqual(secondary_kwargs["ExpressionAttributeNames"]["#fid"], "COD-V1")


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
