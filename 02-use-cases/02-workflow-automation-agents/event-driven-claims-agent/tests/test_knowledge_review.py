"""scripts/knowledge_review.py: approving a draft is explicit, bound to its content, and never silent.

Run:  ./.venv/bin/python3 -m unittest tests.test_knowledge_review -v
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
spec = importlib.util.spec_from_file_location("knowledge_review", ROOT / "scripts" / "knowledge_review.py")
kr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kr)

from core import approval, knowledge_store  # noqa: E402

VOICE = {k: f"{k}" for k in knowledge_store.VOICE_KEYS}
WHO = dict(client="acme", approved_by_name="Owner", approved_by_role="Head", reviewed_by="Rev", now="2026-10-30T10:00:00Z")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = Path(tmp.name)
        draft = self.client / "knowledge" / "drafts" / "d1"
        draft.mkdir(parents=True)
        curated = {"voice": VOICE, "intents": {"other_unknown": {"snippet": "x", "sources": ["s"]}}}
        (draft / "curated.json").write_text(json.dumps(curated), encoding="utf-8")
        (draft / "sources.json").write_text(json.dumps({"s": {"title": "T", "uri": "u"}}), encoding="utf-8")
        (draft / "REVIEW.md").write_text("review", encoding="utf-8")

    def test_approve_copies_records_and_selects(self):
        folder = kr.approve(self.client, "d1", **WHO)
        self.assertEqual(approval.verify_approval(folder, client="acme")["approved_by_name"], "Owner")
        self.assertFalse((folder / "REVIEW.md").exists())
        self.assertEqual((self.client / "knowledge" / "approved" / "CURRENT").read_text().strip(), "d1")

    def test_unknown_draft_refused(self):
        with self.assertRaises(kr.ReviewError):
            kr.approve(self.client, "nope", **WHO)

    def test_invalid_draft_is_not_approved_and_current_is_untouched(self):
        bad = {"voice": VOICE, "intents": {"other_unknown": {"snippet": "x", "sources": []}}}
        (self.client / "knowledge" / "drafts" / "d1" / "curated.json").write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(knowledge_store.KnowledgeError):
            kr.approve(self.client, "d1", **WHO)
        self.assertFalse((self.client / "knowledge" / "approved").exists())

    def test_cannot_approve_the_same_version_twice(self):
        kr.approve(self.client, "d1", **WHO)
        with self.assertRaises(kr.ReviewError):
            kr.approve(self.client, "d1", **WHO)

    def test_blank_approver_refused_and_nothing_left_behind(self):
        with self.assertRaises(approval.ApprovalError):
            kr.approve(self.client, "d1", **{**WHO, "approved_by_name": ""})
        self.assertFalse((self.client / "knowledge" / "approved" / "d1").exists())
        self.assertFalse((self.client / "knowledge" / "approved" / "d1.staging").exists())


if __name__ == "__main__":
    unittest.main()
