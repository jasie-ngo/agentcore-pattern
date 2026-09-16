"""Unit tests for the HESTA structured models and deterministic attachment step."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

import config  # noqa: E402
from agents import disclosure_check  # noqa: E402
from agents.attachment_validation import assess  # noqa: E402
from ingestion.email_normalizer import normalize_email  # noqa: E402
from knowledge import hesta_snippets  # noqa: E402
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

    def test_attachment_present_is_treated_as_present(self):
        inbound = normalize_email(
            "Please find attached my Binding Death Nomination form. [ATTACHMENT form.pdf]"
        )
        result = assess(
            inbound,
            type("Intent", (), {"primary_intent_id": "death_benefit_nomination"})(),
        )
        self.assertEqual(result.status, "present")
        self.assertEqual(result.attachments_present, 1)

    def test_missing_attachment_satisfied_by_earlier_case_history(self):
        """A follow-up with no new attachment doesn't re-flag as missing if the case's
        history shows one was provided earlier — case-wide, not scoped to that intent."""
        inbound = normalize_email("Just checking in on this. Member number 60010001.")
        case_history = [
            {"entry_type": "inbound_member_email", "attachments_present": 0},
            {"entry_type": "inbound_member_email", "attachments_present": 1},
        ]
        result = assess(
            inbound,
            type("Intent", (), {"primary_intent_id": "death_benefit_nomination"})(),
            case_history=case_history,
        )
        self.assertEqual(result.status, "present")
        self.assertEqual(result.attachments_present, 0)
        self.assertIn("already on file", result.notes)

    def test_missing_attachment_with_no_prior_history_stays_missing(self):
        inbound = normalize_email("Please send my Binding Death Nomination form. Member number 60010001.")
        result = assess(
            inbound,
            type("Intent", (), {"primary_intent_id": "death_benefit_nomination"})(),
            case_history=[{"entry_type": "inbound_member_email", "attachments_present": 0}],
        )
        self.assertEqual(result.status, "missing")


class MaxDraftRevisionsConfigTests(unittest.TestCase):
    """TODO 4 rule 2: MAX_DRAFT_REVISIONS must validate as a non-negative bounded integer."""

    def test_default_is_a_small_non_negative_int(self):
        self.assertIsInstance(config.MAX_DRAFT_REVISIONS, int)
        self.assertGreaterEqual(config.MAX_DRAFT_REVISIONS, 0)

    def test_negative_env_value_is_clamped_to_min(self):
        self.assertEqual(config._bounded_int("X", -3, min_value=0, max_value=5), 0)

    def test_oversized_env_value_is_clamped_to_max(self):
        self.assertEqual(config._bounded_int("X", 999, min_value=0, max_value=5), 5)

    def test_non_integer_falls_back_to_default(self):
        import os

        os.environ["_TEST_MAX_DRAFT_REVISIONS"] = "not-a-number"
        try:
            self.assertEqual(
                config._bounded_int("_TEST_MAX_DRAFT_REVISIONS", 2, min_value=0, max_value=5), 2
            )
        finally:
            del os.environ["_TEST_MAX_DRAFT_REVISIONS"]


class DisclosureCheckTests(unittest.TestCase):
    """TODO 5 rule 7: deterministic post-generation sensitive-detail scan."""

    def test_static_boilerplate_never_flags(self):
        static_text = (
            hesta_snippets.LEGAL_FOOTER
            + hesta_snippets.GREETING
            + hesta_snippets.SIGNOFF
            + hesta_snippets.IDENTITY_VERIFICATION_REQUEST
            + hesta_snippets.PERSONAL_ADVICE_DECLINE
            + "".join(hesta_snippets._SNIPPETS.values())
        )
        self.assertEqual(disclosure_check.scan("Re: your HESTA enquiry", static_text), [])

    def test_member_repeating_own_number_does_not_flag(self):
        body = "Thanks, we have your member number 60010001 on file and are reviewing your request."
        self.assertEqual(disclosure_check.scan("", body), [])

    def test_flags_specific_dollar_amount(self):
        findings = disclosure_check.scan("", "Your balance is $12,345.67 as of today.")
        self.assertIn("possible specific dollar amount", findings)

    def test_flags_bsb_number(self):
        findings = disclosure_check.scan("", "Please note our BSB is 063-000 for the transfer.")
        self.assertIn("possible BSB number", findings)

    def test_flags_bank_account_number(self):
        findings = disclosure_check.scan("", "The account number on file is 123456789.")
        self.assertIn("possible bank account number", findings)

    def test_flags_tax_file_number(self):
        findings = disclosure_check.scan("", "Your TFN 123 456 789 has been recorded.")
        self.assertIn("possible tax file number (TFN)", findings)

    def test_findings_are_labels_not_the_matched_value(self):
        findings = disclosure_check.scan("", "Your TFN 123 456 789 has been recorded.")
        joined = " ".join(findings)
        self.assertNotIn("123 456 789", joined)


if __name__ == "__main__":
    unittest.main()
