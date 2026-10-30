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


class ParseEmailBoundaryTests(unittest.TestCase):
    """Characterise the parsing rules that matter because header text is attacker-controlled."""

    def setUp(self):
        self.mod = load()

    def test_crlf_blank_line_ends_headers_and_body_header_is_not_read(self):
        headers, body = self.mod.parse_email(
            "From: a@b.c\r\nMessage-ID: <m@x>\r\n\r\nMessage-ID: <evil@x>\r\nbody\r\n"
        )
        self.assertEqual(headers["message-id"], "<m@x>")
        self.assertEqual(headers["from"], "a@b.c")
        for value in headers.values():
            self.assertNotIn("\r", value)
        self.assertTrue(body.startswith("Message-ID: <evil@x>"))

    def test_three_line_fold_with_spaces_and_tabs_joins_in_order(self):
        headers, _ = self.mod.parse_email("From: a@b.c\nReferences: <a>\n <b>\n\t<c>\n\nbody")
        self.assertEqual(headers["references"], "<a> <b> <c>")

    def test_a_continuation_after_the_blank_line_goes_to_the_body(self):
        headers, body = self.mod.parse_email("From: a@b.c\nReferences: <a>\n\n <late>\nmore")
        self.assertEqual(headers["references"], "<a>")
        self.assertEqual(body, "<late>\nmore")

    def test_the_first_duplicate_wins_and_the_repeat_fold_is_dropped(self):
        headers, _ = self.mod.parse_email("From: a@b.c\nMessage-ID: <1@x>\nMessage-ID: <2@x>\n <3@x>\n\nbody")
        self.assertEqual(headers["message-id"], "<1@x>")

    def test_a_space_before_the_colon_is_not_a_header(self):
        headers, _ = self.mod.parse_email("From: a@b.c\nMessage-ID : <x>\n\nbody")
        self.assertNotIn("message-id", headers)

    def test_a_leading_continuation_with_no_previous_header_is_ignored(self):
        headers, _ = self.mod.parse_email(" <orphan>\nFrom: a@b.c\n\nbody")
        self.assertEqual(headers, {"from": "a@b.c"})

    def test_a_lone_carriage_return_cannot_inject_a_header(self):
        headers, _ = self.mod.parse_email("From: a@b.c\nSubject: s\rMessage-ID: <inj@x>\n\nbody")
        self.assertNotIn("message-id", headers)
        self.assertEqual(headers["subject"], "s\rMessage-ID: <inj@x>")

    def test_a_fold_after_an_unrelated_header_does_not_attach_to_a_known_one(self):
        headers, _ = self.mod.parse_email("From: a@b.c\nReferences: <a>\nX-Foo: bar\n <folded>\n\nbody")
        self.assertEqual(headers["references"], "<a>")

    def test_an_empty_references_followed_by_folded_lines_keeps_the_ids(self):
        headers, _ = self.mod.parse_email("From: a@b.c\nReferences:\n <a>\n <b>\n\nbody")
        self.assertEqual(headers["references"], "<a> <b>")

    def test_an_empty_first_copy_hides_a_later_real_copy(self):
        # Current behaviour: the first Message-ID wins even when it is empty, and empty
        # values are then dropped, so message-id is absent. The sender can only blank
        # their own id this way, so this is accepted.
        headers, _ = self.mod.parse_email("From: a@b.c\nMessage-ID:\nMessage-ID: <real@x>\n\nbody")
        self.assertNotIn("message-id", headers)

    def test_a_whitespace_only_separator_ends_the_headers_before_any_fold(self):
        # The blank-line test must run before the fold test: a line of only a space or
        # tab starts with whitespace, so a fold-first parser would fold it onto
        # References and go on reading the body as headers (reading the injected id).
        for separator in (" \n", "\t\n"):
            with self.subTest(separator=repr(separator)):
                headers, body = self.mod.parse_email(
                    "From: a@b.c\nReferences: <a>\n" + separator + "<x@x> body line\nMessage-ID: <inj@x>\n"
                )
                self.assertEqual(set(headers), {"from", "references"})
                self.assertEqual(headers["references"], "<a>")
                self.assertNotIn("message-id", headers)
                self.assertIn("<x@x> body line", body)
                self.assertIn("Message-ID: <inj@x>", body)

    def test_header_names_match_in_any_case(self):
        text = "From: a@b.c\nmEsSaGe-Id: <m@x>\nIN-REPLY-TO: <r@x>\nrefERENCES: <a> <b>\n\nbody"
        headers, _ = self.mod.parse_email(text)
        self.assertEqual(headers["message-id"], "<m@x>")
        self.assertEqual(headers["in-reply-to"], "<r@x>")
        self.assertEqual(headers["references"], "<a> <b>")
        sent = run_handler(self.mod, text.encode())
        self.assertEqual(sent["headers"]["message_id"], "<m@x>")
        self.assertEqual(sent["headers"]["in_reply_to"], "<r@x>")
        self.assertEqual(sent["headers"]["references"], ["<a>", "<b>"])

    def test_references_in_the_payload_has_no_empty_strings(self):
        sent = run_handler(self.mod, b"From: a@b.c\nReferences: <a>   <b>\n\nbody")
        self.assertEqual(sent["headers"]["references"], ["<a>", "<b>"])
        self.assertNotIn("", sent["headers"]["references"])


if __name__ == "__main__":
    unittest.main()
