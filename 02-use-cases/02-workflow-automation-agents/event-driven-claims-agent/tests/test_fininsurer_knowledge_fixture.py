"""The committed first approved knowledge version for fininsurer is valid, approved, and cites real synthetic files.

Run:  ./.venv/bin/python3 -m unittest tests.test_fininsurer_knowledge_fixture -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core import knowledge_store  # noqa: E402

KNOWLEDGE = ROOT / "clients" / "fininsurer" / "knowledge" / "approved"


class FixtureTests(unittest.TestCase):
    def test_current_points_at_an_approved_folder(self):
        version = (KNOWLEDGE / "CURRENT").read_text(encoding="utf-8").strip()
        k = knowledge_store.load(KNOWLEDGE / version, client="fininsurer")
        self.assertEqual(k.version, version)
        self.assertIn("hardship_premium_relief", k.intents)
        self.assertIn("Do NOT confirm or hint at", k.snippet_for("hardship_premium_relief"))

    def test_every_citation_names_a_synthetic_source_file(self):
        version = (KNOWLEDGE / "CURRENT").read_text(encoding="utf-8").strip()
        k = knowledge_store.parse(KNOWLEDGE / version)
        for source_id, source in k.sources.items():
            self.assertTrue((ROOT / source["uri"]).is_file(), f"{source_id}: {source['uri']} is not a committed synthetic file")


if __name__ == "__main__":
    unittest.main()
