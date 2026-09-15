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

    def test_creates_verified_case(self):
        self.mod.table.query.return_value = {"Items": []}
        result = self.mod.handler({"member_id": "60010001"}, None)
        self.assertEqual(result["status"], "new_case_created")
        self.assertTrue(result["case"]["case_id"].startswith("CASE-"))
        self.assertEqual(result["case"]["member_id"], "60010001")
        self.assertEqual(result["case"]["identity_status"], "verified")
        self.mod.table.put_item.assert_called_once()

    def test_returns_existing_verified_case(self):
        cases = [{"case_id": "CASE-1234", "member_id": "60010001", "status": "Open"}]
        self.mod.table.query.return_value = {"Items": cases}
        result = self.mod.handler({"member_id": "60010001"}, None)
        self.assertEqual(result, {"status": "existing_cases_found", "cases": cases})
        self.mod.table.put_item.assert_not_called()

    def test_creates_anonymous_case_for_unknown_member(self):
        result = self.mod.handler({"sender_email": "unknown@example.com"}, None)
        case = result["case"]
        self.assertEqual(result["status"], "new_case_created")
        self.assertTrue(case["case_id"].startswith("CASE-"))
        self.assertEqual(case["identity_status"], "unverified")
        self.assertEqual(case["sender_email"], "unknown@example.com")
        self.assertNotIn("member_id", case)
        self.mod.table.put_item.assert_called_once()
        self.mod.table.query.assert_not_called()


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


if __name__ == "__main__":
    unittest.main()
