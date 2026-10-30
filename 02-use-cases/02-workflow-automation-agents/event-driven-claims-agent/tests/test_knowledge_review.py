"""scripts/knowledge_review.py: approving a draft is explicit, bound to its content, and never silent.

Run:  ./.venv/bin/python3 -m unittest tests.test_knowledge_review -v
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def _make_draft(self, name):
        draft = self.client / "knowledge" / "drafts" / name
        draft.mkdir(parents=True)
        curated = {"voice": VOICE, "intents": {"other_unknown": {"snippet": "y", "sources": ["s"]}}}
        (draft / "curated.json").write_text(json.dumps(curated), encoding="utf-8")
        (draft / "sources.json").write_text(json.dumps({"s": {"title": "T", "uri": "u"}}), encoding="utf-8")
        return draft

    def _approved(self):
        return self.client / "knowledge" / "approved"

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
        with self.assertRaises(kr.ReviewError) as ctx:
            kr.approve(self.client, "d1", **WHO)
        self.assertIn("approved once", str(ctx.exception))

    def test_blank_approver_refused_and_nothing_left_behind(self):
        with self.assertRaises(approval.ApprovalError):
            kr.approve(self.client, "d1", **{**WHO, "approved_by_name": ""})
        self.assertFalse((self.client / "knowledge" / "approved" / "d1").exists())
        self.assertFalse((self.client / "knowledge" / "approved" / "d1.staging").exists())

    # Fix round 1, item 1: names are validated and nothing is created for a bad one.

    def test_bad_draft_version_names_refused_and_nothing_created(self):
        for bad in ["../x", "/abs/path", "", ".", "CURRENT", "d1.staging", "a/b", "..", "d1\n"]:
            with self.subTest(version=bad):
                with self.assertRaises(kr.ReviewError):
                    kr.approve(self.client, bad, **WHO)
                self.assertFalse(self._approved().exists())
                self.assertFalse((self.client / "knowledge" / "approved").exists())

    def test_bad_client_name_refused_and_nothing_created(self):
        for bad in ["", "../acme", "a/b", " "]:
            with self.subTest(client=bad):
                with self.assertRaises(kr.ReviewError):
                    kr.approve(self.client, "d1", **{**WHO, "client": bad})
                self.assertFalse(self._approved().exists())

    # Fix round 1, item 2: selection is atomic and an approved-but-unselected version can be finished.

    def test_failed_selection_leaves_version_approved_and_retry_selects_it(self):
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                kr.approve(self.client, "d1", **WHO)
        self.assertTrue((self._approved() / "d1" / "approval.json").is_file())
        self.assertFalse((self._approved() / "CURRENT").exists())
        self.assertFalse((self._approved() / "CURRENT.tmp").exists())
        folder = kr.approve(self.client, "d1", **WHO)
        self.assertEqual(folder, self._approved() / "d1")
        self.assertEqual((self._approved() / "CURRENT").read_text(), "d1\n")

    def test_failed_approval_keeps_previous_current_exactly(self):
        kr.approve(self.client, "d1", **WHO)
        before = (self._approved() / "CURRENT").read_text(encoding="utf-8")
        self._make_draft("d2")
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                kr.approve(self.client, "d2", **WHO)
        self.assertEqual((self._approved() / "CURRENT").read_text(encoding="utf-8"), before)
        self.assertEqual(before, "d1\n")
        # the failed version is approved but not selected, so a retry finishes it
        kr.approve(self.client, "d2", **WHO)
        self.assertEqual((self._approved() / "CURRENT").read_text(encoding="utf-8"), "d2\n")

    def test_selected_version_still_refused_as_approved_once(self):
        kr.approve(self.client, "d1", **WHO)
        with self.assertRaises(kr.ReviewError) as ctx:
            kr.approve(self.client, "d1", **WHO)
        self.assertIn("approved once", str(ctx.exception))
        self.assertEqual((self._approved() / "CURRENT").read_text(), "d1\n")

    # Fix round 1, item 3: the copy is validated, and the record binds the validated copy.

    def test_invalid_copy_is_refused_and_leaves_nothing(self):
        real_copytree = shutil.copytree

        def corrupting_copytree(src, dst, *args, **kwargs):
            result = real_copytree(src, dst, *args, **kwargs)
            bad = {"voice": VOICE, "intents": {"other_unknown": {"snippet": "x", "sources": []}}}
            (Path(dst) / "curated.json").write_text(json.dumps(bad), encoding="utf-8")
            return result

        with mock.patch.object(kr.shutil, "copytree", side_effect=corrupting_copytree):
            with self.assertRaises(knowledge_store.KnowledgeError):
                kr.approve(self.client, "d1", **WHO)
        self.assertFalse((self._approved() / "d1").exists())
        self.assertFalse((self._approved() / "d1.staging").exists())
        self.assertFalse((self._approved() / "CURRENT").exists())

    # Fix round 1, item 4: symlinks are refused, a partial copy is cleaned up, and main reports OSError cleanly.

    def test_draft_with_symlink_is_refused(self):
        outside = self.client / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        os.symlink(outside, self.client / "knowledge" / "drafts" / "d1" / "link.txt")
        with self.assertRaises(kr.ReviewError):
            kr.approve(self.client, "d1", **WHO)
        self.assertFalse(self._approved().exists())

    def test_partial_copy_is_cleaned_up(self):
        def partial_copytree(src, dst, *args, **kwargs):
            Path(dst).mkdir(parents=True)
            (Path(dst) / "curated.json").write_text("{", encoding="utf-8")
            raise OSError("copy interrupted")

        with mock.patch.object(kr.shutil, "copytree", side_effect=partial_copytree):
            with self.assertRaises(OSError):
                kr.approve(self.client, "d1", **WHO)
        self.assertFalse((self._approved() / "d1.staging").exists())
        self.assertFalse((self._approved() / "d1").exists())

    def test_main_reports_oserror_and_review_error_with_exit_1(self):
        for exc in [OSError("disk gone"), kr.ReviewError("no draft")]:
            with self.subTest(exc=type(exc).__name__):
                err = io.StringIO()
                with mock.patch.object(kr, "approve", side_effect=exc), contextlib.redirect_stderr(err):
                    code = kr.main(["approve", "acme", "d1", "--approver-name", "O", "--approver-role", "H", "--reviewed-by", "R"])
                self.assertEqual(code, 1)
                self.assertIn("error:", err.getvalue())

    # Fix round 1, item 5: the remaining blank fields, and a stale staging folder.

    def test_blank_role_refused_and_nothing_left_behind(self):
        with self.assertRaises(approval.ApprovalError):
            kr.approve(self.client, "d1", **{**WHO, "approved_by_role": ""})
        self.assertFalse((self._approved() / "d1").exists())
        self.assertFalse((self._approved() / "d1.staging").exists())
        self.assertFalse((self._approved() / "CURRENT").exists())

    def test_blank_reviewer_refused_and_nothing_left_behind(self):
        with self.assertRaises(approval.ApprovalError):
            kr.approve(self.client, "d1", **{**WHO, "reviewed_by": "  "})
        self.assertFalse((self._approved() / "d1").exists())
        self.assertFalse((self._approved() / "d1.staging").exists())
        self.assertFalse((self._approved() / "CURRENT").exists())

    def test_blank_or_whitespace_now_refused_and_nothing_left_behind(self):
        for blank in ["", "   "]:
            with self.subTest(now=blank):
                with self.assertRaises(approval.ApprovalError):
                    kr.approve(self.client, "d1", **{**WHO, "now": blank})
                self.assertFalse((self._approved() / "d1").exists())
                self.assertFalse((self._approved() / "d1.staging").exists())
                self.assertFalse((self._approved() / "CURRENT").exists())

    def test_stale_staging_folder_does_not_block_a_retry(self):
        stale = self._approved() / "d1.staging"
        stale.mkdir(parents=True)
        (stale / "junk.txt").write_text("left over", encoding="utf-8")
        folder = kr.approve(self.client, "d1", **WHO)
        self.assertEqual(approval.verify_approval(folder, client="acme")["approved_by_name"], "Owner")
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
