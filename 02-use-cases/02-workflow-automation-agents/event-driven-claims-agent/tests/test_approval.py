"""Tests for core/approval.py: the approval record for client-owned content baked into an image.

Run:  ./.venv/bin/python3 -m unittest tests.test_approval -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core import approval  # noqa: E402

WHO = dict(approved_by_name="A. Owner", approved_by_role="Head of Hardship", reviewed_by="Reviewer One", approved_at="2026-10-30T09:00:00Z")


class ApprovalTests(unittest.TestCase):
    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "curated.json").write_text('{"a": 1}', encoding="utf-8")
        (root / "sources.json").write_text("{}", encoding="utf-8")
        return root

    def test_round_trip(self):
        root = self.make()
        approval.write_approval(root, client="acme", version="v1", **WHO)
        record = approval.verify_approval(root, client="acme")
        self.assertEqual(record["version"], "v1")
        self.assertEqual(record["content_sha256"], approval.digest_dir(root))

    def test_changed_byte_fails(self):
        root = self.make()
        approval.write_approval(root, client="acme", version="v1", **WHO)
        (root / "curated.json").write_text('{"a": 2}', encoding="utf-8")
        with self.assertRaisesRegex(approval.ApprovalError, "changed since approval"):
            approval.verify_approval(root, client="acme")

    def test_added_file_fails(self):
        root = self.make()
        approval.write_approval(root, client="acme", version="v1", **WHO)
        (root / "extra.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(approval.ApprovalError):
            approval.verify_approval(root, client="acme")

    def test_wrong_client_fails(self):
        root = self.make()
        approval.write_approval(root, client="acme", version="v1", **WHO)
        with self.assertRaisesRegex(approval.ApprovalError, "belongs to"):
            approval.verify_approval(root, client="other")

    def test_missing_record_fails(self):
        root = self.make()
        with self.assertRaisesRegex(approval.ApprovalError, "no approval.json"):
            approval.verify_approval(root, client="acme")

    def test_missing_field_fails(self):
        root = self.make()
        record = approval.write_approval(root, client="acme", version="v1", **WHO)
        del record["approved_by_name"]
        (root / "approval.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(approval.ApprovalError, "approved_by_name"):
            approval.verify_approval(root, client="acme")

    def test_blank_approver_fails(self):
        root = self.make()
        with self.assertRaisesRegex(approval.ApprovalError, "approved_by_name"):
            approval.write_approval(root, client="acme", version="v1", **{**WHO, "approved_by_name": " "})
        self.assertFalse((root / "approval.json").exists())


if __name__ == "__main__":
    unittest.main()
