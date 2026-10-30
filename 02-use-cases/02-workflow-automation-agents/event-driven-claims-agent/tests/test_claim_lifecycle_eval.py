"""The capability eval: replay the scripted threads against the real graph.

Skipped until core/claim_graph.py exists (Task 8 removes the skip and adds the replay). A scenario that cannot pass is a design bug,
not a test to loosen.

Run:  ./.venv/bin/python3 -m unittest tests.test_claim_lifecycle_eval -v
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

SKIP = "core/claim_graph.py is built in Task 7"
try:
    from core import claim_graph  # noqa: F401
    SKIP = ""
except ImportError:
    pass


class ScenarioShapeTests(unittest.TestCase):
    def test_scenarios_are_well_formed(self):
        for scenario in SCENARIOS:
            self.assertEqual(set(scenario), {"name", "emails", "staff", "expect"}, scenario["name"])
            self.assertEqual(set(scenario["expect"]), {"final_state", "claims", "refs_in_subjects"}, scenario["name"])


@unittest.skipIf(SKIP, SKIP)
class ReplayTests(unittest.TestCase):
    pass  # Task 8 adds `test_each_scenario`.


if __name__ == "__main__":
    unittest.main()
