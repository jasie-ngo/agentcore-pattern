"""Tests for the demo's display filter, and one end-to-end run that proves no client name reaches the screen.

Run:
    python3 -m unittest tests.test_demo_anonymise -v

The end-to-end test needs `strands-agents[cedar]` and PyYAML (see demo/requirements.in) and skips with a reason without them.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "demo") not in sys.path:
    sys.path.insert(0, str(ROOT / "demo"))

import anonymise  # noqa: E402

SKIP_REASON = ""
try:
    import cedarpy  # noqa: F401
    import strands  # noqa: F401
    import yaml  # noqa: F401
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    SKIP_REASON = f"needs strands-agents[cedar] and PyYAML ({exc})"


class AnonymiseTests(unittest.TestCase):
    def test_the_client_is_renamed_in_every_spelling(self):
        self.assertEqual(anonymise.clean("HESTA"), "FUND-A")
        self.assertEqual(anonymise.clean("Hesta"), "Fund-A")
        self.assertEqual(anonymise.clean("app/04_hesta_member_email/hesta/tools.py"), "app/04_fund_a_member_email/fund_a/tools.py")
        self.assertEqual(anonymise.clean("hesta-tool-boundary"), "fund-a-tool-boundary")

    def test_text_without_a_name_is_unchanged(self):
        self.assertEqual(anonymise.clean("the tool is never permitted"), "the tool is never permitted")

    def test_a_prospect_is_an_error_not_a_rename(self):
        for text in ("Medibank", "MEDIBANK pack", "the medibank rules"):
            with self.subTest(text=text), self.assertRaises(anonymise.LeakError):
                anonymise.clean(text)

    def test_nothing_survives_cleaning(self):
        self.assertNotIn("hesta", anonymise.clean("Hesta HESTA hesta_x x-hesta").lower())


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class WholeDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = subprocess.run(
            [sys.executable, str(ROOT / "demo" / "run_demo.py"), "--fast", "--no-color"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_it_runs_to_the_end(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr[-800:])
        self.assertIn("== 6. The ask ==", self.result.stdout)

    def test_no_client_name_reaches_the_screen(self):
        # stdout is what the audience sees; check stderr too in case a message is printed there.
        for stream in (self.result.stdout, self.result.stderr):
            self.assertNotIn("hesta", stream.lower())
            self.assertNotIn("medibank", stream.lower())

    def test_every_beat_carries_a_label(self):
        lines = self.result.stdout.splitlines()
        beats = [i for i, line in enumerate(lines) if line.startswith("== ")]
        self.assertEqual(len(beats), 6)
        for i in beats:
            self.assertRegex(lines[i + 1], r"^\s+\[(REAL|SLIDE)")

    def test_the_demo_shows_what_it_claims(self):
        out = self.result.stdout
        self.assertIn("REFUSED", out)                       # beat 1
        self.assertIn("DRIFT fund_a_routing_gate", out)      # beat 2: the finding
        self.assertIn("BLOCKED: the tool never ran", out)   # beats 3 and 4
        self.assertNotIn("CHANGED", out)                    # beat 4: the core did not move
        self.assertIn("19 passed, 0 failed", out)           # beat 4: the same checks on the new pack


if __name__ == "__main__":
    unittest.main()
