"""The trigger Lambda forwards Message-ID, In-Reply-To and References, folded lines included, and nothing else changes.

Run:  ./.venv/bin/python3 -m unittest tests.test_trigger_headers -v
"""

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load():
    with mock.patch.dict(os.environ, {"AWS_DEFAULT_REGION": "ap-southeast-2", "AGENTCORE_RUNTIME_ARN": "arn:test"}):
        spec = importlib.util.spec_from_file_location("trigger_handler", ROOT / "lambdas" / "trigger" / "handler.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


EMAIL = (
    "From: Ann A <ann.a@example.com>\n"
    "Subject: Re: [CLR-1A2B3C4D] Question\n"
    "Message-ID: <m2@x>\n"
    "In-Reply-To: <d1@x>\n"
    "References: <m0@x>\n <d1@x>\n"
    "\n"
    "Thanks, here is more.\n"
)


def run_handler(mod, body: bytes):
    sent = {}
    s3 = mock.Mock()
    s3.get_object.return_value = {"Body": mock.Mock(read=lambda: body)}

    def fake_invoke(payload):
        sent.update(payload)
        return 200, []

    with mock.patch.object(mod, "invoke_runtime_async", fake_invoke), mock.patch.object(mod, "s3", s3):
        mod.handler({"detail": {"bucket": {"name": "b"}, "object": {"key": "k"}}}, None)
    return sent


class HeaderTests(unittest.TestCase):
    def setUp(self):
        self.mod = load()

    def test_parse_email_reads_the_thread_headers(self):
        headers, body = self.mod.parse_email(EMAIL)
        self.assertEqual(headers["message-id"], "<m2@x>")
        self.assertEqual(headers["in-reply-to"], "<d1@x>")
        self.assertEqual(headers["references"].split(), ["<m0@x>", "<d1@x>"])
        self.assertEqual(body, "Thanks, here is more.")

    def test_existing_headers_still_parse(self):
        headers, _ = self.mod.parse_email(EMAIL)
        self.assertIn("subject", headers)
        self.assertIn("from", headers)

    def test_missing_thread_headers_are_absent(self):
        headers, _ = self.mod.parse_email("From: a@b.c\nSubject: Hi\n\nbody")
        self.assertNotIn("in-reply-to", headers)

    def test_payload_gets_a_headers_block_for_email_input(self):
        sent = run_handler(self.mod, EMAIL.encode())
        self.assertEqual(sent["headers"]["message_id"], "<m2@x>")
        self.assertEqual(sent["headers"]["in_reply_to"], "<d1@x>")
        self.assertEqual(sent["headers"]["references"], ["<m0@x>", "<d1@x>"])
        self.assertEqual(sent["headers"]["subject"], "Re: [CLR-1A2B3C4D] Question")
        self.assertIn("prompt", sent)
        self.assertEqual(sent["claimant_email"], "Ann A <ann.a@example.com>")

    def test_a_first_email_has_empty_thread_fields_not_missing_keys(self):
        sent = run_handler(self.mod, b"From: a@b.c\nSubject: Hi\n\nbody")
        self.assertEqual(sent["headers"]["in_reply_to"], "")
        self.assertEqual(sent["headers"]["references"], [])

    def test_json_input_payload_is_unchanged(self):
        sent = run_handler(self.mod, b'{"claimant_email": "x@y.z"}')
        self.assertNotIn("headers", sent)


if __name__ == "__main__":
    unittest.main()
