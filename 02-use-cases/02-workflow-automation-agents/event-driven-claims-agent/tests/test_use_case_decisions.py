"""The claims and HESTA decision tables (app/<use_case>/decisions.yaml).

They replaced hand-written routing code (claims ``routing.py``; HESTA
``classify_verification``, ``assess_attachments`` and ``route_decision``), so these
tests pin the same behaviour. Tables are read with ``load_table``, which fills
``${VAR:-default}`` from the environment — that path needs the agent venv.

Run:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from typing import ClassVar
from unittest import mock

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "app"))

try:
    from _shared import decision_table as dt
    from _shared.local_tools import load_table

    _STRANDS_AVAILABLE = True
except ImportError:
    _STRANDS_AVAILABLE = False


def _path(use_case: str) -> str:
    return os.path.join(_ROOT, "app", use_case, "decisions.yaml")


@unittest.skipUnless(_STRANDS_AVAILABLE, "Strands SDK not installed (run under agent venv)")
class ClaimsRoutingTests(unittest.TestCase):
    def setUp(self):
        self.table = load_table(_path("claims_agent"))

    def _action(self, **inputs):
        return dt.evaluate(self.table, inputs)["outcome"]

    def test_approved_high_confidence_accept_is_auto_approved(self):
        self.assertEqual(self._action(decision="ACCEPT", confidence=92, approved=True), "AUTO_APPROVE")

    def test_threshold_is_inclusive(self):
        self.assertEqual(self._action(decision="ACCEPT", confidence=80, approved=True), "AUTO_APPROVE")
        self.assertEqual(self._action(decision="ACCEPT", confidence=79, approved=True), "HUMAN_REVIEW")

    def test_reject_always_wins(self):
        self.assertEqual(self._action(decision="REJECT", confidence=99, approved=True), "REJECT")

    def test_missing_decision_defaults_to_reject(self):
        self.assertEqual(self._action(decision="", confidence=99, approved=True), "REJECT")

    def test_missing_validation_defaults_to_human_review(self):
        self.assertEqual(self._action(decision="ACCEPT"), "HUMAN_REVIEW")

    def test_not_approved_goes_to_human_review(self):
        self.assertEqual(self._action(decision="ACCEPT", confidence=95, approved=False), "HUMAN_REVIEW")

    def test_threshold_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"AUTO_APPROVE_THRESHOLD": "95"}):
            table = load_table(_path("claims_agent"))
        self.assertEqual(
            dt.evaluate(table, {"decision": "ACCEPT", "confidence": 92, "approved": True})["outcome"], "HUMAN_REVIEW"
        )


@unittest.skipUnless(_STRANDS_AVAILABLE, "Strands SDK not installed (run under agent venv)")
class HestaVerifyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.table = load_table(_path("hesta_agent"), "verify_identity")

    def _verify(self, **inputs):
        return dt.evaluate(self.table, inputs)

    def test_no_number_is_unverified(self):
        result = self._verify(member_number="")
        self.assertEqual((result["outcome"], result["verification_required"]), ("unverified", True))

    def test_matching_email_on_active_record_is_verified(self):
        result = self._verify(
            member_number="POL-1",
            sender_email="Jane@Example.com",
            record_found=True,
            record_email="jane@example.com",
            record_status="active",
        )
        self.assertEqual((result["outcome"], result["verification_required"]), ("verified", False))

    def test_email_mismatch_is_partial(self):
        result = self._verify(
            member_number="POL-1",
            sender_email="x@example.com",
            record_found=True,
            record_email="jane@example.com",
            record_status="active",
        )
        self.assertEqual(result["outcome"], "partial")

    def test_inactive_record_is_unverified(self):
        result = self._verify(
            member_number="POL-1",
            sender_email="jane@example.com",
            record_found=True,
            record_email="jane@example.com",
            record_status="expired",
        )
        self.assertEqual(result["outcome"], "unverified")

    def test_no_record_or_lookup_not_run_is_unverified(self):
        self.assertEqual(self._verify(member_number="POL-1", record_found=False)["rule_id"], "no_record")
        self.assertEqual(self._verify(member_number="POL-1", record_status="active")["rule_id"], "lookup_not_run")


@unittest.skipUnless(_STRANDS_AVAILABLE, "Strands SDK not installed (run under agent venv)")
class HestaAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.table = load_table(_path("hesta_agent"), "check_attachments")

    def _check(self, intent, count):
        return dt.evaluate(self.table, {"primary_intent_id": intent, "attachment_count": count})

    def test_expected_document_missing(self):
        result = self._check("financial_hardship", 0)
        self.assertEqual(
            (result["outcome"], result["expected_document"]), ("missing", "bank statement / evidence of hardship")
        )

    def test_expected_document_present(self):
        self.assertEqual(self._check("death_benefit_nomination", 1)["outcome"], "present_unverified")

    def test_unexpected_attachment(self):
        result = self._check("change_of_details", 2)
        self.assertEqual((result["outcome"], result["expected_document"]), ("present_unverified", "none"))

    def test_nothing_expected_nothing_sent(self):
        self.assertEqual(self._check("rollover_transfer_combine", 0)["outcome"], "not_applicable")


@unittest.skipUnless(_STRANDS_AVAILABLE, "Strands SDK not installed (run under agent venv)")
class HestaRouteCaseTests(unittest.TestCase):
    CLEAN: ClassVar[dict] = {
        "primary_intent_id": "change_of_details",
        "primary_confidence": 90,
        "confident_intent_count": 1,
        "needs_human_triage": False,
        "personal_advice_requested": False,
        "verification_required": False,
        "vulnerability_flag_count": 0,
        "priority": "normal",
    }

    def setUp(self):
        self.table = load_table(_path("hesta_agent"), "route_case")

    def _route(self, **overrides):
        return dt.evaluate(self.table, {**self.CLEAN, **overrides})

    def test_clean_verified_case_is_not_escalated(self):
        result = self._route()
        self.assertEqual((result["outcome"], result["rule_ids"]), ("no_escalation", []))

    def test_every_reason_is_listed(self):
        result = self._route(
            primary_intent_id="financial_hardship",
            personal_advice_requested=True,
            verification_required=True,
            priority="urgent",
        )
        self.assertEqual(result["outcome"], "escalate")
        self.assertEqual(
            result["rule_ids"], ["regulated_intent", "personal_advice", "identity_not_verified", "high_priority"]
        )

    def test_low_confidence_and_multiple_intents(self):
        self.assertEqual(
            self._route(primary_confidence=50, confident_intent_count=2)["rule_ids"],
            ["low_confidence", "multiple_intents"],
        )

    def test_unknown_intent_and_vulnerability(self):
        self.assertEqual(
            self._route(primary_intent_id="other_unknown", vulnerability_flag_count=1)["rule_ids"],
            ["intent_unknown", "vulnerability"],
        )

    def test_unchecked_identity_escalates(self):
        inputs = {k: v for k, v in self.CLEAN.items() if k != "verification_required"}
        self.assertEqual(dt.evaluate(self.table, inputs)["rule_ids"], ["identity_unknown"])

    def test_threshold_in_reason_is_filled_in(self):
        reason = self._route(primary_confidence=10)["matches"][0]["reason"]
        self.assertNotIn("${", reason)
        self.assertIn("70", reason)


if __name__ == "__main__":
    unittest.main()
