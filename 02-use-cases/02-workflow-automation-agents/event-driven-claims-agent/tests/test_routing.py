"""Unit tests for the HESTA human-in-the-loop routing gate."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from models import DetectedIntent, EmpathyAssessment, IntentResult, MemberProfile  # noqa: E402
from routing import decide  # noqa: E402


class HestaRoutingTests(unittest.TestCase):
    def _intent(self, intent_id="other_unknown", confidence=90, **kwargs):
        return IntentResult(
            intents=[DetectedIntent(intent_id=intent_id, confidence=confidence, rationale="test")],
            primary_intent_id=intent_id,
            sender_type="member",
            **kwargs,
        )

    def _profile(self, verified=True):
        return MemberProfile(
            member_number="60010001" if verified else None,
            matched=verified,
            verification_level="verified" if verified else "unverified",
            verification_required=not verified,
        )

    def _empathy(self, **kwargs):
        return EmpathyAssessment(
            sentiment="neutral",
            priority="normal",
            recommended_attention="test",
            **kwargs,
        )

    def test_unverified_member_escalates(self):
        decision = decide(self._intent(), self._profile(False), self._empathy())
        self.assertTrue(decision.escalate_to_human)
        self.assertIn("identity not verified", " ".join(decision.reasons))

    def test_regulated_intent_escalates_even_when_verified(self):
        decision = decide(self._intent("death_benefit_nomination"), self._profile(), self._empathy())
        self.assertTrue(decision.escalate_to_human)
        self.assertTrue(decision.regulated)

    def test_vulnerability_escalates(self):
        decision = decide(
            self._intent("change_of_details"),
            self._profile(),
            self._empathy(vulnerability_flags=["financial_distress"]),
        )
        self.assertTrue(decision.escalate_to_human)

    def test_clear_general_request_can_continue(self):
        decision = decide(self._intent("general_information"), self._profile(), self._empathy())
        self.assertFalse(decision.escalate_to_human)
        self.assertFalse(decision.regulated)

    def test_personal_advice_always_escalates(self):
        decision = decide(
            self._intent("change_of_details", personal_advice_requested=True),
            self._profile(),
            self._empathy(),
        )
        self.assertTrue(decision.escalate_to_human)


if __name__ == "__main__":
    unittest.main()
