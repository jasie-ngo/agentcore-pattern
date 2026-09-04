"""Tests for config.resolve_model_variant (app/hesta-claimsagent/config.py).

Run:
    python3 -m unittest tests.test_hesta_model_routing -v
"""

import os
import sys
import unittest
from unittest.mock import patch

# Clean up module cache BEFORE importing to ensure we get the hesta-claimsagent version,
# not a cached version from another test (e.g., app/claimsagent).
# This allows test_routing.py (for app/claimsagent) and this test to import their own fresh versions.
_saved_config = sys.modules.pop("config", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

import config  # noqa: E402


class ResolveModelVariantNoTableTests(unittest.TestCase):
    """When MODEL_ROUTING_TABLE is unset (today's default), always return the env-var default."""

    def test_fast_role_returns_fast_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", ""):
            model_id, variant = config.resolve_model_variant("fast", seed="case-123")
            self.assertEqual(model_id, config.FAST_MODEL_ID)
            self.assertEqual(variant, "primary")

    def test_strong_role_returns_strong_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", ""):
            model_id, variant = config.resolve_model_variant("strong", seed="case-123")
            self.assertEqual(model_id, config.AGENT_MODEL_ID)
            self.assertEqual(variant, "primary")


class ResolveModelVariantWithTableTests(unittest.TestCase):
    """When a table is configured, canaryPercent controls a deterministic per-seed split."""

    def _mock_item(self, primary="primary-model", canary="canary-model", pct=30):
        return {"role": "fast", "primaryModelId": primary, "canaryModelId": canary, "canaryPercent": pct}

    def test_no_item_for_role_falls_back_to_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch.object(config, "_get_model_routing_item", return_value=None):
            model_id, variant = config.resolve_model_variant("fast", seed="case-123")
            self.assertEqual(model_id, config.FAST_MODEL_ID)
            self.assertEqual(variant, "primary")

    def test_zero_canary_percent_always_primary(self):
        item = self._mock_item(pct=0)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch.object(config, "_get_model_routing_item", return_value=item):
            model_id, variant = config.resolve_model_variant("fast", seed="any-seed")
            self.assertEqual(model_id, "primary-model")
            self.assertEqual(variant, "primary")

    def test_hundred_percent_canary_always_canary(self):
        item = self._mock_item(pct=100)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch.object(config, "_get_model_routing_item", return_value=item):
            model_id, variant = config.resolve_model_variant("fast", seed="any-seed")
            self.assertEqual(model_id, "canary-model")
            self.assertEqual(variant, "canary")

    def test_same_seed_is_deterministic(self):
        item = self._mock_item(pct=50)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch.object(config, "_get_model_routing_item", return_value=item):
            first = config.resolve_model_variant("fast", seed="case-456")
            second = config.resolve_model_variant("fast", seed="case-456")
            self.assertEqual(first, second)

    def test_lookup_exception_degrades_to_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch.object(config, "_get_model_routing_item", side_effect=RuntimeError("dynamodb unavailable")):
            model_id, variant = config.resolve_model_variant("strong", seed="case-789")
            self.assertEqual(model_id, config.AGENT_MODEL_ID)
            self.assertEqual(variant, "primary")

    def test_malformed_canary_percent_degrades_to_default(self):
        """Malformed item data (non-numeric canaryPercent) is treated like lookup failure."""
        item = {"role": "fast", "primaryModelId": "primary-model", "canaryModelId": "canary-model", "canaryPercent": "not-a-number"}
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch.object(config, "_get_model_routing_item", return_value=item):
            model_id, variant = config.resolve_model_variant("fast", seed="case-123")
            self.assertEqual(model_id, config.FAST_MODEL_ID)
            self.assertEqual(variant, "primary")


def tearDownModule():
    """Restore config to sys.modules after this module completes.

    `unittest discover` imports every test module up front while building the suite,
    before any test or teardown runs — so the pop-before-import at the top of this file
    is what prevents this module from picking up a cached config from another test
    (e.g., app/claimsagent) during discovery. This function does not undo any
    contamination from discovery (that already happened, if it was going to); its only
    job is to restore whatever was previously in sys.modules so it doesn't leak into
    whichever test module happens to run or import next.
    """
    if _saved_config is not None:
        sys.modules["config"] = _saved_config
    elif "config" in sys.modules:
        sys.modules.pop("config", None)


if __name__ == "__main__":
    unittest.main()
