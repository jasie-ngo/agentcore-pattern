"""Unit tests for the HESTA structured models and deterministic attachment step."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from agents.attachment_validation import assess  # noqa: E402
from ingestion.email_normalizer import normalize_email  # noqa: E402
from models import AttachmentAssessment, MemberProfile  # noqa: E402
from intents import taxonomy  # noqa: E402


class HestaModelTests(unittest.TestCase):
    def test_member_profile_uses_member_id_contract(self):
        profile = MemberProfile(
            member_number="60010001",
            matched=True,
            match_key="member_id",
            factors_matched=["member_id", "email"],
            verification_level="verified",
            verification_required=False,
        )
        self.assertEqual(profile.match_key, "member_id")
        self.assertEqual(profile.factors_matched, ["member_id", "email"])

    def test_attachment_missing_for_expected_document(self):
        inbound = normalize_email("Please send my Binding Death Nomination form. Member number 60010001.")
        result = assess(
            inbound,
            type("Intent", (), {"primary_intent_id": "death_benefit_nomination"})(),
        )
        self.assertIsInstance(result, AttachmentAssessment)
        self.assertEqual(result.status, "missing")
        self.assertEqual(result.expected_document, taxonomy.expected_attachment("death_benefit_nomination"))

    def test_attachment_present_is_unverified(self):
        inbound = normalize_email(
            "Please find attached my Binding Death Nomination form. [ATTACHMENT form.pdf]"
        )
        result = assess(
            inbound,
            type("Intent", (), {"primary_intent_id": "death_benefit_nomination"})(),
        )
        self.assertEqual(result.status, "present_unverified")
        self.assertEqual(result.attachments_present, 1)


if __name__ == "__main__":
    unittest.main()
