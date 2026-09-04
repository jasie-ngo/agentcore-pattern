"""Tests for the HESTA pilot's routing gate (app/hesta-claimsagent/routing.py).

Run:
    python3 -m unittest tests.test_hesta_routing -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from models import AttachmentAssessment, EmpathyAssessment, IntentResult, DetectedIntent, MemberProfile  # noqa: E402
from routing import decide  # noqa: E402

# Clean up module cache to avoid conflicts when other tests import their own 'routing' module.
# This allows test_routing.py (for app/claimsagent) to import fresh without conflicts.
_saved_routing = sys.modules.pop("routing", None)
_saved_models = sys.modules.pop("models", None)


def _intent(primary="change_of_details", confidence=90, needs_triage=False):
    return IntentResult(
        intents=[DetectedIntent(intent_id=primary, confidence=confidence, rationale="test", evidence_quote="")],
        primary_intent_id=primary,
        sender_type="member",
        needs_human_triage=needs_triage,
    )


def _profile(verified=True):
    return MemberProfile(
        member_number="M1",
        matched=True,
        verification_level="verified" if verified else "unverified",
        verification_required=not verified,
    )


def _empathy():
    return EmpathyAssessment(sentiment="neutral", priority="normal", recommended_attention="Standard service.")


class AttachmentRoutingTests(unittest.TestCase):
    def test_missing_required_attachment_escalates(self):
        attach = AttachmentAssessment(
            attachments_present=0,
            expected_document="bank statement / evidence of hardship",
            status="missing",
            notes="Expected a bank statement, none detected.",
        )
        decision = decide(_intent(primary="financial_hardship"), _profile(), _empathy(), attach)
        self.assertTrue(decision.escalate_to_human)
        self.assertTrue(any("missing" in r.lower() or "attachment" in r.lower() for r in decision.reasons))

    def test_present_unverified_does_not_force_escalation_alone(self):
        # Pilot can't validate content — presence-but-unverified alone shouldn't force
        # escalation on its own (it's a note for staff, not a hard gate).
        attach = AttachmentAssessment(
            attachments_present=1,
            expected_document="bank statement / evidence of hardship",
            status="present_unverified",
            notes="1 attachment detected; expected a bank statement.",
        )
        decision = decide(_intent(primary="rollover_transfer_combine"), _profile(), _empathy(), attach)
        # Non-regulated, verified, high confidence, no vulnerability, attachment present
        # (even if unverified) → no escalation reason should come from attachment status.
        self.assertFalse(any("attachment" in r.lower() for r in decision.reasons))

    def test_not_applicable_does_not_escalate(self):
        attach = AttachmentAssessment(
            attachments_present=0, expected_document="none", status="not_applicable", notes="none expected"
        )
        decision = decide(_intent(primary="rollover_transfer_combine"), _profile(), _empathy(), attach)
        self.assertFalse(any("attachment" in r.lower() for r in decision.reasons))

    def test_missing_attachment_escalates_even_when_otherwise_clean(self):
        # Non-regulated intent, verified, high confidence, no vulnerability — the ONLY
        # escalation trigger is the missing attachment. Proves attachment status alone
        # can now drive escalate_to_human=True (the gap this task closes).
        attach = AttachmentAssessment(
            attachments_present=0,
            expected_document="court order / legal documents",
            status="missing",
            notes="Expected court order, none detected.",
        )
        decision = decide(_intent(primary="rollover_transfer_combine"), _profile(), _empathy(), attach)
        self.assertTrue(decision.escalate_to_human)


if __name__ == "__main__":
    unittest.main()
