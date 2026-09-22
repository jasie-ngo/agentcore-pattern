"""Unit tests for the Trigger Lambda's pure email-parsing helpers.

Covers `parse_email` and `is_email_format`, plus the MIME `.eml` parsing added
for TODO 2 (`parse_eml`, `_is_mime_message`, `_from_address`), in
lambdas/trigger/handler.py — the stdlib-only functions that turn an S3 object
into a structured prompt (+ real attachments). The handler imports boto3 (lazy
clients, no network at import), so importing it is safe without AWS credentials.

Run:
    python3 -m unittest discover -s tests
"""

import email
import email.policy
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "trigger"))

from handler import (  # noqa: E402
    _from_address,
    _is_mime_message,
    is_email_format,
    parse_email,
    parse_eml,
)


def _make_mime_message(body: str, attachments: list[tuple[str, str, bytes]] | None = None):
    """Build a multipart/mixed EmailMessage — always multipart, even with zero attachments
    (see D1 / TODO 7): forces the trigger's MIME detection regardless of attachment count."""
    msg = email.message.EmailMessage(policy=email.policy.default)
    msg["From"] = "Sarah Thompson <sarah@example.com>"
    msg["Subject"] = "Binding Death Nomination"
    msg.set_content(body)
    msg.make_mixed()
    for filename, content_type, data in attachments or []:
        maintype, subtype = content_type.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename, cte="7bit")
    return msg


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


class MimeDetectionTests(unittest.TestCase):
    def test_multipart_message_is_detected_as_mime(self):
        msg = _make_mime_message("hello")
        self.assertTrue(_is_mime_message(msg))

    def test_legacy_plain_text_is_not_detected_as_mime(self):
        """TODO 2 rule 5: a plain .txt object (even one shaped like an email, with From:/
        Subject: header lines) must NOT be misdetected as MIME — it keeps its legacy path."""
        legacy = email.message_from_string(
            "From: a@b.com\nSubject: hi\n\nbody text", policy=email.policy.default
        )
        self.assertFalse(_is_mime_message(legacy))

    def test_contact_form_text_is_not_detected_as_mime(self):
        legacy = email.message_from_string(
            "You've received a new form based mail...\nValues:\nmessage: hi", policy=email.policy.default
        )
        self.assertFalse(_is_mime_message(legacy))


class ParseEmlTests(unittest.TestCase):
    def test_one_text_attachment_yields_one_readable_entry(self):
        msg = _make_mime_message("Please see attached.", [("form.txt", "text/plain", b"HESTA-FORM-ID: BDBN-NOM-V1")])
        body, attachments = parse_eml(msg)
        self.assertIn("Please see attached", body)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["filename"], "form.txt")
        self.assertEqual(attachments[0]["text"], "HESTA-FORM-ID: BDBN-NOM-V1")
        self.assertIsNone(attachments[0]["skipped_reason"])

    def test_no_attachments_yields_empty_list(self):
        msg = _make_mime_message("Just a message, nothing attached.")
        _, attachments = parse_eml(msg)
        self.assertEqual(attachments, [])

    def test_oversized_attachment_is_reported_not_truncated(self):
        import handler as trigger_handler

        oversized = b"x" * (trigger_handler.MAX_ATTACHMENT_BYTES + 1)
        msg = _make_mime_message("here", [("big.txt", "text/plain", oversized)])
        _, attachments = parse_eml(msg)
        self.assertEqual(len(attachments), 1)
        self.assertIsNone(attachments[0]["text"])
        self.assertIn("exceeds", attachments[0]["skipped_reason"])

    def test_non_text_attachment_is_recorded_with_no_guessed_text(self):
        msg = _make_mime_message("here", [("scan.pdf", "application/pdf", b"%PDF-1.4 fake")])
        _, attachments = parse_eml(msg)
        self.assertEqual(len(attachments), 1)
        self.assertIsNone(attachments[0]["text"])
        self.assertIn("unsupported content type", attachments[0]["skipped_reason"])

    def test_attachments_beyond_the_limit_are_kept_not_dropped(self):
        import handler as trigger_handler

        attachments_in = [
            (f"f{i}.txt", "text/plain", b"content") for i in range(trigger_handler.MAX_ATTACHMENTS + 2)
        ]
        msg = _make_mime_message("here", attachments_in)
        _, attachments = parse_eml(msg)
        self.assertEqual(len(attachments), trigger_handler.MAX_ATTACHMENTS + 2)
        over_limit = attachments[trigger_handler.MAX_ATTACHMENTS:]
        self.assertTrue(all(a["text"] is None and a["skipped_reason"] for a in over_limit))


class FromAddressTests(unittest.TestCase):
    def test_extracts_bare_address_from_display_name(self):
        msg = _make_mime_message("hi")
        self.assertEqual(_from_address(msg), "sarah@example.com")

    def test_missing_from_header_yields_empty_string(self):
        msg = email.message.EmailMessage(policy=email.policy.default)
        msg.set_content("hi")
        self.assertEqual(_from_address(msg), "")


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class HandlerIntegrationTests(unittest.TestCase):
    """End-to-end handler() behaviour (S3 read → parse → Runtime payload), with S3 and the
    Runtime invocation mocked — no AWS credentials or network needed."""

    def setUp(self):
        import handler as trigger_handler

        self.trigger_handler = trigger_handler
        self._original_s3 = trigger_handler.s3
        self._original_invoke = trigger_handler.invoke_runtime_async
        self.trigger_handler.s3 = unittest.mock.MagicMock()
        self.captured_payload = {}

        def _fake_invoke(payload_dict):
            self.captured_payload.update(payload_dict)
            return 200, ["ok"]

        self.trigger_handler.invoke_runtime_async = _fake_invoke

    def tearDown(self):
        self.trigger_handler.s3 = self._original_s3
        self.trigger_handler.invoke_runtime_async = self._original_invoke

    def _event(self):
        return {"detail": {"bucket": {"name": "b"}, "object": {"key": "claims-inbox/msg.eml"}}}

    def test_eml_with_attachment_is_parsed_and_forwarded(self):
        msg = _make_mime_message(
            "Please find attached my form.",
            [("bdbn.txt", "text/plain", b"HESTA-FORM-ID: BDBN-NOM-V1")],
        )
        self.trigger_handler.s3.get_object.return_value = {
            "Body": _FakeBody(msg.as_bytes()),
            "ETag": '"abc123"',
        }
        result = self.trigger_handler.handler(self._event(), None)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(len(self.captured_payload["attachments"]), 1)
        self.assertEqual(self.captured_payload["attachments"][0]["filename"], "bdbn.txt")
        self.assertEqual(self.captured_payload["claimant_email"], "sarah@example.com")

    def test_legacy_txt_object_still_yields_no_attachments(self):
        content = b"From: a@b.com\nSubject: hi\n\nMy basement flooded."
        self.trigger_handler.s3.get_object.return_value = {"Body": _FakeBody(content), "ETag": '"x"'}
        result = self.trigger_handler.handler(self._event(), None)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(self.captured_payload["attachments"], [])
        self.assertIn("My basement flooded", self.captured_payload["prompt"])

    def test_binary_object_is_a_handled_failure_not_a_crash(self):
        binary = b"\xff\xfe\x00\x01not valid utf-8 or mime \x80\x81"
        self.trigger_handler.s3.get_object.return_value = {"Body": _FakeBody(binary), "ETag": '"y"'}
        result = self.trigger_handler.handler(self._event(), None)
        self.assertEqual(result["statusCode"], 422)
        self.assertEqual(self.captured_payload, {})  # Runtime was never invoked


if __name__ == "__main__":
    unittest.main()
