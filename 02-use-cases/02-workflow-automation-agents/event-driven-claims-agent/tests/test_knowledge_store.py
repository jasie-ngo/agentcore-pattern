"""Tests for core/knowledge_store.py.

Run:  ./.venv/bin/python3 -m unittest tests.test_knowledge_store -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core import approval, knowledge_store as ks  # noqa: E402

VOICE = {k: f"{k} text" for k in ks.VOICE_KEYS}
WHO = dict(approved_by_name="A. Owner", approved_by_role="Owner", reviewed_by="Rev", approved_at="2026-10-30T00:00:00Z")


def write(root: Path, curated=None, sources=None):
    curated = curated or {
        "voice": VOICE,
        "intents": {
            "other_unknown": {"snippet": "ask a question", "sources": ["s1"]},
            "claim_status": {"snippet": "say what is on file", "sources": ["s1"]},
        },
    }
    (root / "curated.json").write_text(json.dumps(curated), encoding="utf-8")
    (root / "sources.json").write_text(json.dumps(sources or {"s1": {"title": "T", "uri": "u"}}), encoding="utf-8")


class StoreTests(unittest.TestCase):
    def root(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_parse_valid(self):
        root = self.root()
        write(root)
        k = ks.parse(root)
        self.assertEqual(k.snippet_for("claim_status"), "say what is on file")
        self.assertEqual(k.snippet_for("nonsense"), "ask a question")  # falls back to other_unknown

    def test_load_requires_approval(self):
        root = self.root()
        write(root)
        with self.assertRaises(approval.ApprovalError):
            ks.load(root, client="acme")
        approval.write_approval(root, client="acme", version="v1", **WHO)
        self.assertEqual(ks.load(root, client="acme").version, "v1")

    def test_load_fails_closed_after_tamper(self):
        root = self.root()
        write(root)
        approval.write_approval(root, client="acme", version="v1", **WHO)
        (root / "sources.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(approval.ApprovalError):
            ks.load(root, client="acme")

    def test_rejects_uncited_intent(self):
        root = self.root()
        write(root, curated={"voice": VOICE, "intents": {"other_unknown": {"snippet": "x", "sources": []}}})
        with self.assertRaisesRegex(ks.KnowledgeError, "other_unknown.*at least one source"):
            ks.parse(root)

    def test_rejects_unknown_source_id(self):
        root = self.root()
        write(root, curated={"voice": VOICE, "intents": {"other_unknown": {"snippet": "x", "sources": ["ghost"]}}})
        with self.assertRaisesRegex(ks.KnowledgeError, "ghost"):
            ks.parse(root)

    def test_rejects_missing_voice_key(self):
        root = self.root()
        voice = {k: v for k, v in VOICE.items() if k != "signoff"}
        write(root, curated={"voice": voice, "intents": {"other_unknown": {"snippet": "x", "sources": ["s1"]}}})
        with self.assertRaisesRegex(ks.KnowledgeError, "signoff"):
            ks.parse(root)

    def test_rejects_missing_fallback_intent(self):
        root = self.root()
        write(root, curated={"voice": VOICE, "intents": {"claim_status": {"snippet": "x", "sources": ["s1"]}}})
        with self.assertRaisesRegex(ks.KnowledgeError, "other_unknown"):
            ks.parse(root)

    def test_rejects_blank_snippet(self):
        root = self.root()
        write(root, curated={"voice": VOICE, "intents": {"other_unknown": {"snippet": "  ", "sources": ["s1"]}}})
        with self.assertRaises(ks.KnowledgeError):
            ks.parse(root)

    def test_missing_folder(self):
        with self.assertRaisesRegex(ks.KnowledgeError, "not found"):
            ks.parse(Path("/nonexistent/knowledge"))

    def test_rejects_non_string_snippet(self):
        for snippet in (None, 123, ["x"], {"a": 1}):
            with self.subTest(snippet=snippet):
                root = self.root()
                write(root, curated={"voice": VOICE, "intents": {"other_unknown": {"snippet": snippet, "sources": ["s1"]}}})
                with self.assertRaisesRegex(ks.KnowledgeError, "other_unknown has no snippet"):
                    ks.parse(root)

    def test_rejects_non_object_curated(self):
        for raw in ("[]", "null", "3"):
            with self.subTest(curated=raw):
                root = self.root()
                (root / "curated.json").write_text(raw, encoding="utf-8")
                (root / "sources.json").write_text(json.dumps({"s1": {"title": "T", "uri": "u"}}), encoding="utf-8")
                with self.assertRaisesRegex(ks.KnowledgeError, "curated.json needs"):
                    ks.parse(root)

    def test_rejects_malformed_json(self):
        root = self.root()
        (root / "curated.json").write_text("{", encoding="utf-8")
        (root / "sources.json").write_text(json.dumps({"s1": {"title": "T", "uri": "u"}}), encoding="utf-8")
        with self.assertRaisesRegex(ks.KnowledgeError, "not valid JSON"):
            ks.parse(root)

    def test_rejects_non_string_source_id(self):
        for source_id in (["x"], {"a": 1}, 5):
            with self.subTest(source_id=source_id):
                root = self.root()
                write(root, curated={"voice": VOICE, "intents": {"other_unknown": {"snippet": "x", "sources": [source_id]}}})
                with self.assertRaisesRegex(ks.KnowledgeError, "other_unknown cites"):
                    ks.parse(root)

    def test_rejects_blank_voice_value(self):
        root = self.root()
        voice = dict(VOICE, greeting="   ")
        write(root, curated={"voice": voice, "intents": {"other_unknown": {"snippet": "x", "sources": ["s1"]}}})
        with self.assertRaisesRegex(ks.KnowledgeError, "voice.greeting"):
            ks.parse(root)

    def test_rejects_non_dict_intent(self):
        root = self.root()
        write(root, curated={"voice": VOICE, "intents": {"other_unknown": "just text"}})
        with self.assertRaisesRegex(ks.KnowledgeError, "other_unknown has no snippet"):
            ks.parse(root)


if __name__ == "__main__":
    unittest.main()
