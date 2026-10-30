"""The capability eval: replay the scripted threads against the real graph.

Skipped until core/claim_graph.py exists. Task 8 writes the replay and replaces the placeholder test, which fails on purpose
while the graph exists, so the eval cannot pass by accident. A scenario that cannot pass is a design bug,
not a test to loosen.

Run:  app/04_hesta_member_email/.venv/bin/python3 -m unittest tests.test_claim_lifecycle_eval -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
for path in (APP, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from claim_scenarios import SCENARIOS  # noqa: E402

EMAIL_KEYS = {"from", "subject", "message_id", "in_reply_to", "references", "pipeline"}
STAFF_KEYS = {"after_email", "event", "expects", "draft_message_id", "refused"}
STAFF_EVENTS = {"staff_released", "staff_resolved", "staff_closed", "member_silent"}

SKIP = ""
try:
    import core.claim_graph  # noqa: F401
except ModuleNotFoundError as exc:
    # Only the graph module being absent skips the eval. A missing dependency inside it (for example langgraph) must fail.
    if exc.name != "core.claim_graph":
        raise
    SKIP = "core/claim_graph.py does not exist yet (Task 7); the replay is written in Task 8"


class ScenarioShapeTests(unittest.TestCase):
    def test_scenarios_are_well_formed(self):
        names = [scenario["name"] for scenario in SCENARIOS]
        self.assertEqual(len(names), len(set(names)), "scenario names must be unique")
        for scenario in SCENARIOS:
            name = scenario["name"]
            self.assertEqual(set(scenario), {"name", "emails", "staff", "expect"}, name)
            self.assertEqual(set(scenario["expect"]), {"final_state", "claims", "refs_in_subjects"}, name)
            self.assertTrue(scenario["emails"], name)
            for index, email in enumerate(scenario["emails"]):
                self.assertEqual(set(email), EMAIL_KEYS, name)
                self.assertEqual(set(email["pipeline"]), {"escalate", "expects"}, name)
                if index == 0:
                    self.assertNotIn("{ref}", email["subject"], f"{name}: first email cannot use {{ref}}")
            for step in scenario["staff"]:
                self.assertLessEqual(set(step), STAFF_KEYS, name)
                self.assertIn("after_email", step, name)
                self.assertIn("event", step, name)
                self.assertTrue(1 <= step["after_email"] <= len(scenario["emails"]), name)
                self.assertIn(step["event"], STAFF_EVENTS, name)
                if "refused" in step:
                    self.assertIs(step["refused"], True, name)


class ReplayTests(unittest.TestCase):
    @unittest.skipIf(SKIP, SKIP)
    def test_each_scenario(self):
        self.fail("replay not implemented: Task 8 must replace this placeholder")


if __name__ == "__main__":
    unittest.main()
