"""The pipeline's models are pinned: changing them is a deliberate act, not a side effect.

ADR-0015 (Q9) pins the models for the pre-sale profile. The pin lives in config.yaml as the default of two variables, which an
environment variable can still override at deploy time, so this test makes the *default* a checked fact. To change a model:
edit PINNED below AND show the change passes the evaluation harness (a different model changes structured-output behaviour and the
confidence numbers the routing gate reads). Do not just edit config.yaml.

Run:
    python3 -m unittest tests.test_model_pin -v
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "app" / "04_hesta_member_email" / "config.yaml"

PINNED = {
    "primary_model": "global.anthropic.claude-sonnet-4-6",
    "fast_model": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}


def _default(expression: str) -> str:
    """The default in a `${NAME:-default}` expression."""
    match = re.fullmatch(r"\$\{[A-Z_]+:-(.+)\}", expression)
    assert match, f"{expression!r} is not a ${{NAME:-default}} expression"
    return match.group(1)


class ModelPinTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    def test_the_default_models_are_the_pinned_ones(self):
        for name, pinned in PINNED.items():
            with self.subTest(variable=name):
                self.assertEqual(_default(self.config["vars"][name]), pinned)

    def test_every_agent_chooses_a_model_by_alias_never_by_id(self):
        for name, agent in self.config["agents"].items():
            with self.subTest(agent=name):
                self.assertIn(agent["model"], self.config["models"])
                self.assertNotIn("model_id", agent)

    def test_the_pin_check_can_fail(self):
        with self.assertRaises(AssertionError):
            _default("not-an-expression")


if __name__ == "__main__":
    unittest.main()
