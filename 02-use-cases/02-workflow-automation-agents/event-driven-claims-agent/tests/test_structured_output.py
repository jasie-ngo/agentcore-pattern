"""Unit tests for the HESTA structured models and deterministic attachment step."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

import config  # noqa: E402
from agents import disclosure_check  # noqa: E402
from agents.attachment_validation import assess  # noqa: E402
from forms import catalog, parser  # noqa: E402
from ingestion.email_normalizer import normalize_email  # noqa: E402
from knowledge import hesta_snippets  # noqa: E402
from models import AttachmentAssessment, MemberProfile  # noqa: E402
from intents import taxonomy  # noqa: E402


class _FakeIntent:
    def __init__(self, intent_id):
        self.primary_intent_id = intent_id


def _filled_form(intent_id: str, *, drop_field: str | None = None, values: dict[str, str] | None = None) -> str:
    """Fill in a blank catalog template for `intent_id`, leaving `drop_field` blank."""
    spec = catalog.form_for(intent_id)
    text = catalog.blank_template(intent_id)
    defaults = {
        "Member name": "Sarah Thompson",
        "Member email": "sarah@example.com",
        "Member number": "60010001",
    }
    if values:
        defaults.update(values)
    for field in spec.fields:
        value = "______________________________" if field.label == drop_field else defaults.get(
            field.label, "Some detail text"
        )
        text = text.replace("______________________________", value, 1)
    return text


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

    def test_marker_convention_is_gone(self):
        """D5: a body that still contains a literal [ATTACHMENT ...] marker reports zero
        attachments — only entries in the real `attachments` list count."""
        inbound = normalize_email("Please find attached my form. [ATTACHMENT form.pdf]")
        self.assertEqual(inbound.attachment_count, 0)
        self.assertEqual(inbound.attachments, [])

    def test_attachment_missing_for_expected_document(self):
        inbound = normalize_email("Please send my BDBN form. Member number 60010001.", attachments=[])
        result = assess(inbound, _FakeIntent("death_benefit_nomination"))
        self.assertIsInstance(result, AttachmentAssessment)
        self.assertEqual(result.status, "missing")
        self.assertEqual(result.expected_document, taxonomy.expected_attachment("death_benefit_nomination"))

    def test_attachment_valid_when_form_matches_and_is_filled_in(self):
        filled = _filled_form("death_benefit_nomination")
        inbound = normalize_email(
            "Please find attached my form.",
            attachments=[{"filename": "bdbn.txt", "content_type": "text/plain", "text": filled}],
        )
        result = assess(inbound, _FakeIntent("death_benefit_nomination"))
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.attachments_present, 1)
        self.assertEqual(result.form_id, "BDBN-NOM-V1")
        self.assertIn("not been semantically verified", result.notes)

    def test_matching_form_wins_regardless_of_attachment_order(self):
        """A member attaching their correct filled form PLUS an unrelated one must still be
        told 'valid' — the matching form wins no matter which MIME part comes first."""
        correct = _filled_form("death_benefit_nomination")
        wrong = _filled_form("financial_hardship")

        inbound_wrong_first = normalize_email(
            "here",
            attachments=[
                {"filename": "fh.txt", "content_type": "text/plain", "text": wrong},
                {"filename": "bdbn.txt", "content_type": "text/plain", "text": correct},
            ],
        )
        result_a = assess(inbound_wrong_first, _FakeIntent("death_benefit_nomination"))
        self.assertEqual(result_a.status, "valid")
        self.assertEqual(result_a.form_id, "BDBN-NOM-V1")

        inbound_correct_first = normalize_email(
            "here",
            attachments=[
                {"filename": "bdbn.txt", "content_type": "text/plain", "text": correct},
                {"filename": "fh.txt", "content_type": "text/plain", "text": wrong},
            ],
        )
        result_b = assess(inbound_correct_first, _FakeIntent("death_benefit_nomination"))
        self.assertEqual(result_b.status, "valid")

    def test_attachment_incomplete_names_the_blank_field(self):
        filled = _filled_form("death_benefit_nomination", drop_field="Nomination details")
        inbound = normalize_email(
            "here", attachments=[{"filename": "bdbn.txt", "content_type": "text/plain", "text": filled}]
        )
        result = assess(inbound, _FakeIntent("death_benefit_nomination"))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.missing_fields, ["Nomination details"])

    def test_attachment_wrong_form_names_both_forms(self):
        wrong = _filled_form("financial_hardship")
        inbound = normalize_email(
            "here", attachments=[{"filename": "fh.txt", "content_type": "text/plain", "text": wrong}]
        )
        result = assess(inbound, _FakeIntent("death_benefit_nomination"))
        self.assertEqual(result.status, "wrong_form")
        self.assertIn("Binding Death Benefit Nomination Form", result.notes)
        self.assertIn("Financial Hardship Form", result.notes)

    def test_attachment_unreadable_when_undecodable(self):
        inbound = normalize_email(
            "here",
            attachments=[
                {"filename": "form.pdf", "content_type": "application/pdf", "text": None, "skipped_reason": "unsupported content type"}
            ],
        )
        result = assess(inbound, _FakeIntent("death_benefit_nomination"))
        self.assertEqual(result.status, "unreadable")

    def test_attachment_unreadable_when_not_a_hesta_form(self):
        inbound = normalize_email(
            "here", attachments=[{"filename": "note.txt", "content_type": "text/plain", "text": "just some notes"}]
        )
        result = assess(inbound, _FakeIntent("death_benefit_nomination"))
        self.assertEqual(result.status, "unreadable")

    def test_unreadable_note_reports_all_reasons_not_just_one(self):
        """Regression: a mix of 'too large' and 'not a form' must both surface, not just
        whichever one `any_readable` happened to pick last."""
        inbound = normalize_email(
            "here",
            attachments=[
                {"filename": "big.pdf", "content_type": "application/pdf", "text": None, "skipped_reason": "attachment exceeds the 262144 byte limit (300000 bytes)"},
                {"filename": "note.txt", "content_type": "text/plain", "text": "just some notes"},
            ],
        )
        result = assess(inbound, _FakeIntent("death_benefit_nomination"))
        self.assertEqual(result.status, "unreadable")
        self.assertIn("exceeds the 262144 byte limit", result.notes)
        self.assertIn("not recognisable as a HESTA form", result.notes)

    def test_not_applicable_when_no_form_for_intent(self):
        inbound = normalize_email("hi", attachments=[])
        result = assess(inbound, _FakeIntent("other_unknown"))
        self.assertEqual(result.status, "not_applicable")

    def test_missing_attachment_satisfied_by_earlier_valid_form_in_case_history(self):
        """A follow-up with no new attachment doesn't re-flag as missing if the case's
        history shows a *validated valid* form on file earlier — case-wide."""
        inbound = normalize_email("Just checking in on this. Member number 60010001.", attachments=[])
        case_history = [{"entry_type": "inbound_member_email", "attachment_status": "valid"}]
        result = assess(inbound, _FakeIntent("death_benefit_nomination"), case_history=case_history)
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.attachments_present, 0)
        self.assertIn("already on file", result.notes)

    def test_missing_attachment_not_satisfied_by_earlier_incomplete_form(self):
        """TODO 4 rule 5: raising the bar — a previously incomplete/wrong form does NOT
        count as already on file, only a previously valid one does."""
        inbound = normalize_email("Just checking in on this. Member number 60010001.", attachments=[])
        case_history = [{"entry_type": "inbound_member_email", "attachment_status": "incomplete"}]
        result = assess(inbound, _FakeIntent("death_benefit_nomination"), case_history=case_history)
        self.assertEqual(result.status, "missing")

    def test_missing_attachment_with_no_prior_history_stays_missing(self):
        inbound = normalize_email("Please send my BDBN form. Member number 60010001.", attachments=[])
        result = assess(
            inbound,
            _FakeIntent("death_benefit_nomination"),
            case_history=[{"entry_type": "inbound_member_email", "attachment_status": "missing"}],
        )
        self.assertEqual(result.status, "missing")


class FormCatalogTests(unittest.TestCase):
    """TODO 1 acceptance criteria: catalog/template consistency."""

    def test_every_intent_resolves_to_a_form(self):
        for intent in taxonomy.INTENTS:
            spec = catalog.form_for(intent.id)
            self.assertIsNotNone(spec, f"missing FormSpec for {intent.id}")
            self.assertTrue(catalog.blank_template(intent.id))

    def test_other_unknown_has_no_form(self):
        self.assertIsNone(catalog.form_for("other_unknown"))

    def test_form_ids_are_unique(self):
        form_ids = [catalog.form_for(i.id).form_id for i in taxonomy.INTENTS]
        self.assertEqual(len(form_ids), len(set(form_ids)))

    def test_template_declares_exactly_its_spec_fields(self):
        for intent in taxonomy.INTENTS:
            spec = catalog.form_for(intent.id)
            text = catalog.blank_template(intent.id)
            self.assertIn(f"HESTA-FORM-ID: {spec.form_id}", text)
            for field in spec.fields:
                self.assertIn(field.label, text)

    def test_field_colons_are_column_aligned(self):
        """Cosmetic, but member-facing: every 'Label : value' line in a template should
        have its colon in the same column, not just parse correctly."""
        for intent in taxonomy.INTENTS:
            text = catalog.blank_template(intent.id)
            colon_columns = {
                line.index(":")
                for line in text.splitlines()
                if ":" in line and "______" in line
            }
            self.assertEqual(len(colon_columns), 1, f"{intent.id} template has misaligned field colons")


class FormParserTests(unittest.TestCase):
    def test_blank_template_fields_are_blank(self):
        parsed = parser.parse_form(catalog.blank_template("death_benefit_nomination"))
        self.assertEqual(parsed.form_id, "BDBN-NOM-V1")
        for value in parsed.fields.values():
            self.assertTrue(parser.is_blank(value))

    def test_bracketed_placeholder_is_blank(self):
        self.assertTrue(parser.is_blank("[MEMBER NAME]"))

    def test_filled_value_is_not_blank(self):
        self.assertFalse(parser.is_blank("Sarah Thompson"))

    def test_non_form_text_returns_none(self):
        self.assertIsNone(parser.parse_form("just a plain note with no header"))


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
