"""Unit tests for the Trigger Lambda's parsing, routing and normalising helpers.

Covers `parse_email`, `is_email_format`, `use_case_for_key` and `build_envelope`
in lambdas/trigger/handler.py — the functions that turn an S3 object into the
envelope the agent receives. The handler imports boto3 (lazy clients, no network
at import), so importing it is safe without AWS credentials. From the repo it
reads ingestion settings from agentcore/generated/manifest.json.

Run:
    python3 -m unittest discover -s tests
"""

import json
import os
import sys
import unittest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "trigger"))

from handler import INGESTION, build_envelope, is_email_format, parse_email, use_case_for_key  # noqa: E402


class IsEmailFormatTests(unittest.TestCase):
    def test_detects_from_header(self):
        self.assertTrue(is_email_format("From: a@b.com\nSubject: hi\n\nbody"))

    def test_detects_subject_header(self):
        self.assertTrue(is_email_format("Subject: Claim\n\nbody"))

    def test_case_insensitive(self):
        self.assertTrue(is_email_format("from: a@b.com\n\nbody"))

    def test_plain_json_is_not_email(self):
        self.assertFalse(is_email_format('{"policy_number": "POL-1"}'))

    def test_plain_text_is_not_email(self):
        self.assertFalse(is_email_format("Just some text about a claim."))

    def test_empty_is_not_email(self):
        self.assertFalse(is_email_format(""))


class ParseEmailTests(unittest.TestCase):
    def test_extracts_headers_and_body(self):
        content = (
            "From: jane@example.com\n"
            "Subject: Water damage claim\n"
            "Date: Mon, 1 Jun 2026 12:00:00 +0000\n"
            "\n"
            "My basement flooded. Policy POL-67890. Approx $8000."
        )
        headers, body = parse_email(content)
        self.assertEqual(headers["from"], "jane@example.com")
        self.assertEqual(headers["subject"], "Water damage claim")
        self.assertIn("POL-67890", body)
        self.assertNotIn("From:", body)

    def test_body_only_after_blank_line(self):
        content = "From: a@b.com\nSubject: s\n\nline1\nline2"
        _, body = parse_email(content)
        self.assertEqual(body, "line1\nline2")

    def test_headers_lowercased_keys(self):
        content = "FROM: a@b.com\nSUBJECT: Hi\n\nbody"
        headers, _ = parse_email(content)
        self.assertIn("from", headers)
        self.assertIn("subject", headers)

    def test_no_headers_yields_empty_dict(self):
        # No blank line and no recognized headers → body_start stays 0.
        headers, body = parse_email("just a body with no headers")
        self.assertEqual(headers, {})

    def test_ignores_unrecognized_headers(self):
        content = "From: a@b.com\nX-Custom: ignore-me\n\nbody"
        headers, _ = parse_email(content)
        self.assertIn("from", headers)
        self.assertNotIn("x-custom", headers)



class UseCaseRoutingTests(unittest.TestCase):
    def test_folder_selects_use_case(self):
        self.assertEqual(use_case_for_key("claims-inbox/claims_agent/a.json"), "claims_agent")
        self.assertEqual(use_case_for_key("claims-inbox/hesta_agent/b.txt"), "hesta_agent")

    def test_no_or_unknown_folder_uses_default(self):
        default = INGESTION["default_use_case"]
        self.assertEqual(use_case_for_key("claims-inbox/a.eml"), default)
        self.assertEqual(use_case_for_key("claims-inbox/unknown/a.eml"), default)


class BuildEnvelopeTests(unittest.TestCase):
    def test_email_headers_reach_the_envelope(self):
        content = "From: Jane Doe <jane@example.com>\nSubject: Hail damage\n\nPlease process POL-12345, $4000."
        env = build_envelope(content, "bucket", "claims-inbox/claims_agent/x.eml")
        self.assertEqual(env["use_case"], "claims_agent")
        self.assertEqual(env["from_email"], "jane@example.com")
        self.assertEqual(env["subject"], "Hail damage")
        self.assertEqual(env["id_for_lookup"], "POL-12345")
        self.assertEqual(env["source"], "s3://bucket/claims-inbox/claims_agent/x.eml")

    def test_json_claim_uses_claimant_email(self):
        content = json.dumps({"policy_number": "POL-67890", "claimant_email": "j@x.co", "amount": 100})
        env = build_envelope(content, "b", "claims-inbox/claims_agent/c.json")
        self.assertEqual((env["from_email"], env["id_for_lookup"]), ("j@x.co", "POL-67890"))

    def test_sample_payload_prompt_is_unwrapped(self):
        content = json.dumps({"prompt": "Member number: 60030001 please help", "sender_email": "a@b.co"})
        env = build_envelope(content, "b", "claims-inbox/hesta_agent/p.json")
        self.assertEqual(env["latest_message"], "Member number: 60030001 please help")
        self.assertEqual(env["id_for_lookup"], "60030001")

    def test_envelope_is_json_serialisable(self):
        json.dumps(build_envelope("plain text", "b", "claims-inbox/x.txt"))


if __name__ == "__main__":
    unittest.main()
