"""Tests for config.resolve_model_variant (app/hesta-claimsagent/config.py).

Run:
    python3 -m unittest tests.test_hesta_model_routing -v
"""

import os
import sys
import unittest
from unittest.mock import patch

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
             patch("config._get_model_routing_item", return_value=None):
            model_id, variant = config.resolve_model_variant("fast", seed="case-123")
            self.assertEqual(model_id, config.FAST_MODEL_ID)
            self.assertEqual(variant, "primary")

    def test_zero_canary_percent_always_primary(self):
        item = self._mock_item(pct=0)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", return_value=item):
            model_id, variant = config.resolve_model_variant("fast", seed="any-seed")
            self.assertEqual(model_id, "primary-model")
            self.assertEqual(variant, "primary")

    def test_hundred_percent_canary_always_canary(self):
        item = self._mock_item(pct=100)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", return_value=item):
            model_id, variant = config.resolve_model_variant("fast", seed="any-seed")
            self.assertEqual(model_id, "canary-model")
            self.assertEqual(variant, "canary")

    def test_same_seed_is_deterministic(self):
        item = self._mock_item(pct=50)
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", return_value=item):
            first = config.resolve_model_variant("fast", seed="case-456")
            second = config.resolve_model_variant("fast", seed="case-456")
            self.assertEqual(first, second)

    def test_lookup_exception_degrades_to_default(self):
        with patch.object(config, "MODEL_ROUTING_TABLE", "ModelRouting"), \
             patch("config._get_model_routing_item", side_effect=RuntimeError("dynamodb unavailable")):
            model_id, variant = config.resolve_model_variant("strong", seed="case-789")
            self.assertEqual(model_id, config.AGENT_MODEL_ID)
            self.assertEqual(variant, "primary")


if __name__ == "__main__":
    unittest.main()
