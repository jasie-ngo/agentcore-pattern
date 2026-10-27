"""Unit tests for the generic normaliser (app/_shared/ingestion.py) and the
use cases' ingestion.yaml settings, run against the real sample emails.

Standard library only for the normaliser; the YAML settings are read from the
generated manifest, so PyYAML is not needed either.

Run:
    python3 -m unittest discover -s tests
"""

import glob
import json
import os
import sys
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "app", "_shared"))

from ingestion import normalize

with open(os.path.join(_ROOT, "agentcore", "generated", "manifest.json"), encoding="utf-8") as _fh:
    _CONFIGS = json.load(_fh)["ingestion"]
HESTA = _CONFIGS["hesta_agent"]
CLAIMS = _CONFIGS["claims_agent"]


def _sample(name: str) -> str:
    with open(os.path.join(_ROOT, "hesta", "sample-emails", name), encoding="utf-8") as fh:
        return fh.read()


class HestaContactFormTests(unittest.TestCase):
    def setUp(self):
        self.env = normalize(_sample("COD_60030001_update_mobile_no_access.txt"), HESTA, use_case="hesta_agent")

    def test_form_fields(self):
        self.assertEqual(self.env["channel"], "contact_form")
        self.assertEqual(self.env["sender_type"], "member")
        self.assertEqual(self.env["from_email"], "tom.fletcher@example.com")
        self.assertEqual(self.env["sender_name"], "Tom Fletcher")
        self.assertEqual(self.env["form_reason"], "Updating my account details")

    def test_member_number(self):
        self.assertEqual(self.env["id_for_lookup"], "60030001")

    def test_envelope_shape(self):
        self.assertEqual(self.env["envelope_version"], 1)
        self.assertEqual(self.env["use_case"], "hesta_agent")
        self.assertTrue(self.env["raw_text"].startswith("You've received"))


class HestaAllSamplesTests(unittest.TestCase):
    def test_every_sample_yields_a_member_number_and_sender(self):
        # The sample files are named <INTENT>_<member number>_..., so the expected
        # number is known for each one.
        for path in sorted(glob.glob(os.path.join(_ROOT, "hesta", "sample-emails", "*.txt"))):
            name = os.path.basename(path)
            with self.subTest(sample=name):
                with open(path, encoding="utf-8") as fh:
                    env = normalize(fh.read(), HESTA)
                self.assertEqual(env["id_for_lookup"], name.split("_")[1])
                self.assertIsNotNone(env["from_email"])
                self.assertTrue(env["latest_message"])


class HestaEmailTests(unittest.TestCase):
    def test_strips_banner_footer_and_quoted_history(self):
        raw = (
            "WARNING: This email originated from outside the organisation.\n"
            "Hi, please update my address. Member number: 60030003\n\n"
            "From: HESTA <noreply@hesta.com.au>\nSent: Monday\nOld quoted reply\n"
            "Issued by H.E.S.T. Australia Ltd ABN 66 006 818 695"
        )
        env = normalize(raw, HESTA, sender_email="a@b.co")
        self.assertEqual(env["latest_message"], "Hi, please update my address. Member number: 60030003")
        self.assertEqual((env["from_email"], env["id_for_lookup"]), ("a@b.co", "60030003"))
        self.assertEqual((env["channel"], env["sender_type"]), ("direct_email", "member"))

    def test_solicitor_detected(self):
        env = normalize("Dear Sir/Madam,\nWe refer to the above matter.", HESTA)
        self.assertEqual((env["channel"], env["sender_type"]), ("third_party", "solicitor"))

    def test_placeholder_is_not_an_id(self):
        env = normalize("Member number: [MEMBER NUMBER]. Please call me.", HESTA)
        self.assertIsNone(env["id_for_lookup"])
        self.assertEqual(env["id_raw"], None)

    def test_attachments_counted(self):
        self.assertEqual(normalize("See [ATTACHMENT form.pdf] and [Attachment id.jpg]", HESTA)["attachment_count"], 2)


class ClaimsTests(unittest.TestCase):
    def test_policy_number_and_sender(self):
        env = normalize("Process this claim: hail damage on POL-12345, $4000", CLAIMS, sender_email="j@x.co")
        self.assertEqual(env["id_for_lookup"], "POL-12345")
        self.assertEqual(env["latest_message"], "hail damage on POL-12345, $4000")
        self.assertEqual(env["sender_type"], "claimant")

    def test_claims_settings_ignore_hesta_patterns(self):
        # A bare digit run is a HESTA member number, not a claims policy number.
        self.assertIsNone(normalize("my number is 60030001", CLAIMS)["id_for_lookup"])


class EmptyConfigTests(unittest.TestCase):
    def test_generic_conventions_only(self):
        env = normalize("Hello\n\nOn Mon, Jane wrote:\n> old", {}, sender_email=None)
        self.assertEqual(env["latest_message"], "Hello")
        self.assertEqual(env["sender_type"], "unknown")
        self.assertIsNone(env["id_for_lookup"])


if __name__ == "__main__":
    unittest.main()
