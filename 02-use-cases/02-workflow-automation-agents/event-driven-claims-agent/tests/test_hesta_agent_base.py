"""Tests for agents.base.build_agent's model_id_override parameter.

Run:
    python3 -m unittest tests.test_hesta_agent_base -v
"""

import os
import sys
import unittest

# Clean up module cache BEFORE importing to ensure we get the hesta-claimsagent version,
# not a cached version from another test (e.g., app/claimsagent).
# This allows test_routing.py (for app/claimsagent) and this test to import their own fresh versions.
_saved_config = sys.modules.pop("config", None)
_saved_agents_base = sys.modules.pop("agents.base", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

import config  # noqa: E402
from agents.base import _build_model  # noqa: E402


class BuildModelOverrideTests(unittest.TestCase):
    def test_no_override_uses_fast_default(self):
        model = _build_model(fast=True, guarded=False, model_id_override=None)
        self.assertEqual(model.config["model_id"], config.FAST_MODEL_ID)

    def test_no_override_uses_strong_default(self):
        model = _build_model(fast=False, guarded=False, model_id_override=None)
        self.assertEqual(model.config["model_id"], config.AGENT_MODEL_ID)

    def test_override_takes_precedence_over_fast(self):
        model = _build_model(fast=True, guarded=False, model_id_override="canary-model-xyz")
        self.assertEqual(model.config["model_id"], "canary-model-xyz")

    def test_override_takes_precedence_over_strong(self):
        model = _build_model(fast=False, guarded=False, model_id_override="canary-model-xyz")
        self.assertEqual(model.config["model_id"], "canary-model-xyz")


def tearDownModule():
    """Restore config and agents.base to sys.modules after this module completes.

    `unittest discover` imports every test module up front while building the suite,
    before any test or teardown runs — so the pop-before-import at the top of this file
    is what prevents this module from picking up a cached config/agents.base from
    another test (e.g., app/claimsagent) during discovery. This function does not undo
    any contamination from discovery (that already happened, if it was going to); its
    only job is to restore whatever was previously in sys.modules so it doesn't leak
    into whichever test module happens to run or import next.
    """
    if _saved_config is not None:
        sys.modules["config"] = _saved_config
    elif "config" in sys.modules:
        sys.modules.pop("config", None)

    if _saved_agents_base is not None:
        sys.modules["agents.base"] = _saved_agents_base
    elif "agents.base" in sys.modules:
        sys.modules.pop("agents.base", None)


if __name__ == "__main__":
    unittest.main()
