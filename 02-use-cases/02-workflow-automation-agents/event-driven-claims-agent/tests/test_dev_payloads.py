"""Tests for the .dev.json fixtures generated alongside hesta/sample-emails/eml/*.eml.

Verifies fixture consistency (every .eml has a matching .dev.json whose
attachments equal what parse_eml() returns for that .eml) and that each
fixture drives normalize_email() + attachment_validation.assess() to the
expected status. Doesn't import main.py (needs bedrock_agentcore) — calls the
normalizer and validator directly, same approach as test_trigger.py.

Run:
    python3 -m unittest discover -s tests
"""

import email
import email.policy
import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "lambdas", "trigger"))
sys.path.insert(0, os.path.join(ROOT, "app", "hesta-claimsagent"))

from handler import parse_eml  # noqa: E402

from agents import attachment_validation  # noqa: E402
from ingestion.email_normalizer import normalize_email  # noqa: E402

EML_DIR = os.path.join(ROOT, "hesta", "sample-emails", "eml")

# Mirrors _INTENT_CODE in scripts/generate_sample_emails.py.
_CODE_TO_INTENT = {
    "BDBN": "death_benefit_nomination",
    "BP": "withdrawal_benefit_payment",
    "COD": "change_of_details",
    "DASP": "departing_australia_payment",
    "FH": "financial_hardship",
    "FLS": "family_law_split",
    "NOI": "notice_of_intent_tax_deduction",
    "RTC": "rollover_transfer_combine",
}

_EXPECTED_STATUS = {
    "filled": "valid",
    "incomplete": "incomplete",
    "wrongform": "wrong_form",
    "noattachment": "missing",
}


def _eml_fixtures():
    return sorted(f for f in os.listdir(EML_DIR) if f.endswith(".eml"))


class _FakeIntentResult:
    def __init__(self, primary_intent_id):
        self.primary_intent_id = primary_intent_id


class DevJsonFixtureConsistencyTests(unittest.TestCase):
    def test_every_eml_has_a_dev_json_and_vice_versa(self):
        emls = {f[: -len(".eml")] for f in os.listdir(EML_DIR) if f.endswith(".eml")}
        jsons = {f[: -len(".dev.json")] for f in os.listdir(EML_DIR) if f.endswith(".dev.json")}
        self.assertEqual(emls, jsons)

    def test_dev_json_attachments_match_parse_eml(self):
        for fname in _eml_fixtures():
            with self.subTest(fname=fname):
                eml_path = os.path.join(EML_DIR, fname)
                json_path = eml_path[: -len(".eml")] + ".dev.json"
                with open(eml_path, "rb") as fh:
                    msg = email.message_from_bytes(fh.read(), policy=email.policy.default)
                _, expected_attachments = parse_eml(msg)

                with open(json_path, encoding="utf-8") as fh:
                    payload = json.load(fh)

                self.assertEqual(payload["attachments"], expected_attachments)
                self.assertEqual(payload["source"], f"eml:{fname}")
                self.assertNotIn("idempotency_key", payload)
                self.assertNotIn("source_object_id", payload)


class DevJsonValidationStatusTests(unittest.TestCase):
    def test_expected_attachment_status_per_fixture(self):
        checked = 0
        for fname in _eml_fixtures():
            stem = fname[: -len(".eml")]
            code = stem.split("_", 1)[0]
            suffix = stem.rsplit("_", 1)[-1]
            if suffix not in _EXPECTED_STATUS or code not in _CODE_TO_INTENT:
                continue

            with self.subTest(fname=fname):
                json_path = os.path.join(EML_DIR, stem + ".dev.json")
                with open(json_path, encoding="utf-8") as fh:
                    payload = json.load(fh)

                inbound = normalize_email(
                    payload["prompt"],
                    sender_email=payload.get("claimant_email"),
                    source=payload.get("source"),
                    attachments=payload.get("attachments"),
                )
                intent_id = _CODE_TO_INTENT[code]
                result = attachment_validation.assess(inbound, _FakeIntentResult(intent_id))
                self.assertEqual(result.status, _EXPECTED_STATUS[suffix])
                checked += 1

        self.assertGreater(checked, 0, "no fixtures matched the expected naming convention")


if __name__ == "__main__":
    unittest.main()
