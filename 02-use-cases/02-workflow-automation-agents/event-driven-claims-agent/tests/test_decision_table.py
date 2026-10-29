"""Tests for decision tables (app/_shared/decision_table.py), run against the
card-dispute use case's real decisions.yaml.

The evaluator is standard library only; PyYAML is needed to read the table.

Run:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from typing import ClassVar

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "app", "_shared"))

import decision_table as dt

try:
    import yaml

    with open(os.path.join(_ROOT, "app", "card_dispute_agent", "decisions.yaml"), encoding="utf-8") as _fh:
        BANK = yaml.safe_load(_fh)
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

VALID_ACTIVE = {
    "intent": "card_dispute",
    "assessment": "valid_dispute",
    "customer_status": "active",
    "card_status": "active",
    "fraud_signals": False,
    "prior_disputes_90d": 0,
    "review_approved": True,
}


@unittest.skipUnless(_YAML_AVAILABLE, "PyYAML not installed")
class CardDisputeDecisionTests(unittest.TestCase):
    """One case per sample email in app/card_dispute_agent/sample_emails/."""

    def _decide(self, **overrides):
        return dt.evaluate(BANK, {**VALID_ACTIVE, **overrides})

    def test_table_is_valid(self):
        dt.validate(BANK)

    def test_small_valid_dispute_gets_provisional_credit(self):
        d = self._decide(amount=42.50)
        self.assertEqual(
            (d["outcome"], d["escalate"], d["rule_id"]), ("provisional_credit", False, "small_valid_dispute")
        )

    def test_large_valid_dispute_goes_to_officer(self):
        self.assertEqual(self._decide(amount="1249.00")["outcome"], "human_review")

    def test_repeat_disputer_goes_to_officer(self):
        self.assertEqual(self._decide(amount=20, prior_disputes_90d=3)["outcome"], "human_review")

    def test_fraud_blocks_card(self):
        d = self._decide(amount=250, fraud_signals="true")
        self.assertEqual((d["outcome"], d["escalate"]), ("block_card_and_escalate", True))

    def test_genuine_purchase_declined_with_officer_check(self):
        d = self._decide(assessment="likely_genuine_purchase", amount=29.95)
        self.assertEqual((d["outcome"], d["escalate"]), ("decline_with_explanation", True))

    def test_suspended_account_beats_everything_but_unknown_customer(self):
        d = self._decide(customer_status="suspended", fraud_signals=True, amount=10)
        self.assertEqual(d["rule_id"], "account_not_active")

    def test_unknown_customer_asks_for_details(self):
        d = self._decide(assessment="customer_not_found", customer_status=None)
        self.assertEqual((d["outcome"], d["escalate"]), ("request_information", False))

    def test_other_intent_is_routed(self):
        self.assertEqual(dt.evaluate(BANK, {"intent": "lost_or_stolen_card"})["outcome"], "route_to_service_team")

    def test_review_rejected_or_missing_goes_to_officer(self):
        self.assertEqual(self._decide(amount=10, review_approved=False)["rule_id"], "review_not_approved")
        self.assertEqual(self._decide(amount=10, review_approved=None)["rule_id"], "review_missing")

    def test_inputs_are_coerced_and_reported(self):
        d = self._decide(amount="42.5", prior_disputes_90d="1")
        self.assertEqual((d["inputs"]["amount"], d["inputs"]["prior_disputes_90d"]), (42.5, 1))


class OperatorTests(unittest.TestCase):
    TABLE: ClassVar[dict] = {
        "name": "t",
        "description": "d",
        "inputs": {"n": {"type": "number"}, "s": {"type": "string"}},
        "rules": [
            {"id": "empty", "when": {"s": {"is_empty": True}}, "then": {"outcome": "empty"}},
            {"id": "range", "when": {"n": {"gte": 10, "lt": 20}}, "then": {"outcome": "teens"}},
            {"id": "listed", "when": {"s": {"in": ["a", "b"]}}, "then": {"outcome": "ab"}},
        ],
        "default": {"outcome": "none"},
    }

    def test_operators(self):
        self.assertEqual(dt.evaluate(self.TABLE, {"s": ""})["outcome"], "empty")
        self.assertEqual(dt.evaluate(self.TABLE, {"s": "z", "n": 15})["outcome"], "teens")
        self.assertEqual(dt.evaluate(self.TABLE, {"s": "z", "n": 20})["outcome"], "none")
        self.assertEqual(dt.evaluate(self.TABLE, {"s": "b", "n": 1})["outcome"], "ab")

    def test_missing_input_fails_conditions(self):
        self.assertEqual(dt.evaluate(self.TABLE, {"s": "z"})["outcome"], "none")

    def test_unparseable_number_is_treated_as_missing(self):
        self.assertIsNone(dt.evaluate(self.TABLE, {"s": "z", "n": "lots"})["inputs"]["n"])


class SameAsAndCollectTests(unittest.TestCase):
    SAME: ClassVar[dict] = {
        "name": "t",
        "description": "d",
        "inputs": {"a": {"type": "string"}, "b": {"type": "string"}},
        "rules": [{"id": "same", "when": {"a": {"same_as": "b"}}, "then": {"outcome": "match"}}],
        "default": {"outcome": "differ"},
    }
    COLLECT: ClassVar[dict] = {
        "name": "t",
        "description": "d",
        "hit_policy": "collect",
        "inputs": {"n": {"type": "integer"}, "flag": {"type": "boolean"}},
        "rules": [
            {"id": "big", "when": {"n": {"gt": 10}}, "then": {"outcome": "escalate", "reason": "big"}},
            {"id": "flagged", "when": {"flag": True}, "then": {"outcome": "escalate", "reason": "flagged"}},
        ],
        "default": {"outcome": "ok"},
    }

    def test_same_as_ignores_case_and_spaces(self):
        self.assertEqual(dt.evaluate(self.SAME, {"a": " Jo@Example.com", "b": "jo@example.com"})["outcome"], "match")
        self.assertEqual(dt.evaluate(self.SAME, {"a": "jo@example.com", "b": "x@example.com"})["outcome"], "differ")

    def test_same_as_missing_other_side_fails(self):
        self.assertEqual(dt.evaluate(self.SAME, {"a": "jo@example.com"})["outcome"], "differ")

    def test_same_as_must_name_an_input(self):
        bad = {**self.SAME, "rules": [{"id": "r", "when": {"a": {"same_as": "zz"}}, "then": {"outcome": "y"}}]}
        with self.assertRaisesRegex(ValueError, "compares with 'zz'"):
            dt.validate(bad)

    def test_collect_returns_every_match(self):
        result = dt.evaluate(self.COLLECT, {"n": 50, "flag": True})
        self.assertEqual((result["outcome"], result["rule_ids"]), ("escalate", ["big", "flagged"]))
        self.assertEqual([m["reason"] for m in result["matches"]], ["big", "flagged"])

    def test_collect_with_no_match_is_the_default(self):
        result = dt.evaluate(self.COLLECT, {"n": 1, "flag": False})
        self.assertEqual((result["outcome"], result["matches"], result["rule_id"]), ("ok", [], "default"))

    def test_unknown_hit_policy(self):
        with self.assertRaisesRegex(ValueError, "hit_policy"):
            dt.validate({**self.COLLECT, "hit_policy": "priority"})


class ValidationTests(unittest.TestCase):
    BASE: ClassVar[dict] = {
        "name": "t",
        "description": "d",
        "inputs": {"a": {"type": "string"}},
        "default": {"outcome": "x"},
    }

    def test_unknown_input_in_rule(self):
        with self.assertRaisesRegex(ValueError, "not one of the inputs"):
            dt.validate({**self.BASE, "rules": [{"id": "r", "when": {"b": 1}, "then": {"outcome": "y"}}]})

    def test_unknown_operator(self):
        with self.assertRaisesRegex(ValueError, "unknown operator"):
            dt.validate({**self.BASE, "rules": [{"id": "r", "when": {"a": {"like": "x"}}, "then": {"outcome": "y"}}]})

    def test_default_required(self):
        with self.assertRaisesRegex(ValueError, "default"):
            dt.validate({k: v for k, v in self.BASE.items() if k != "default"})

    def test_duplicate_rule_ids(self):
        rule = {"id": "r", "when": {}, "then": {"outcome": "y"}}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            dt.validate({**self.BASE, "rules": [rule, rule]})


if __name__ == "__main__":
    unittest.main()
