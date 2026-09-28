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

os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
try:
    import importlib.util as _importlib_util

    _case_lookup_spec = _importlib_util.spec_from_file_location(
        "case_lookup_creation_handler_for_fixtures",
        os.path.join(ROOT, "lambdas", "case_lookup_creation", "handler.py"),
    )
    _case_lookup_mod = _importlib_util.module_from_spec(_case_lookup_spec)
    _case_lookup_spec.loader.exec_module(_case_lookup_mod)
    _CASE_LOOKUP_AVAILABLE = True
except ImportError:
    _case_lookup_mod = None
    _CASE_LOOKUP_AVAILABLE = False

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


@unittest.skipUnless(_CASE_LOOKUP_AVAILABLE, "boto3 not installed")
class ThreadFixtureCaseIdentityTests(unittest.TestCase):
    """TODO 7: dropping thread_1 then thread_2 must resolve to ONE case; the different-subject
    fixture for the same member must resolve to a DIFFERENT case."""

    def _case_id_for(self, fname: str) -> str:
        json_path = os.path.join(EML_DIR, fname[: -len(".eml")] + ".dev.json")
        with open(json_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        inbound = normalize_email(
            payload["prompt"],
            sender_email=payload.get("claimant_email"),
            source=payload.get("source"),
            attachments=payload.get("attachments"),
            subject=payload.get("subject"),
        )
        return _case_lookup_mod._case_id("60010001", inbound.thread_key)

    def test_thread_1_and_thread_2_resolve_to_the_same_case(self):
        self.assertEqual(
            self._case_id_for("BDBN_60010001_thread_1.eml"),
            self._case_id_for("BDBN_60010001_thread_2.eml"),
        )

    def test_otherthread_resolves_to_a_different_case(self):
        self.assertNotEqual(
            self._case_id_for("BDBN_60010001_thread_1.eml"),
            self._case_id_for("BDBN_60010001_otherthread.eml"),
        )


if __name__ == "__main__":
    unittest.main()
