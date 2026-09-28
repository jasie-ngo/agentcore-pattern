"""Unit tests for pure helper functions in main.py.

main.py imports bedrock_agentcore/strands/mcp (the full Runtime stack), so this whole file is
skipped when those aren't installed — same convention as tests/test_memory_session.py.

Run:
    python3 -m unittest discover -s tests
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "app", "hesta-claimsagent"))

try:
    import main  # noqa: E402

    _MAIN_AVAILABLE = True
except ImportError:
    main = None
    _MAIN_AVAILABLE = False


@unittest.skipUnless(_MAIN_AVAILABLE, "bedrock_agentcore/strands/mcp not installed")
class FilterOwnRetryEntriesTests(unittest.TestCase):
    """A retry of the same inbound email (same idempotency_key) must not see its own earlier
    partial Memory write as prior conversation."""

    def test_strips_entries_owned_by_this_idempotency_key(self):
        history = [
            {"entry_id": "old-key:inbound", "entry_type": "inbound_member_email", "content": "earlier email"},
            {"entry_id": "retry-key:inbound", "entry_type": "inbound_member_email", "content": "this email"},
            {"entry_id": "retry-key:draft", "entry_type": "generated_draft", "content": "this draft"},
        ]
        result = main._filter_own_retry_entries(history, "retry-key")
        self.assertEqual([e["entry_id"] for e in result], ["old-key:inbound"])

    def test_no_entries_owned_by_this_key_leaves_history_unchanged(self):
        history = [{"entry_id": "old-key:inbound", "entry_type": "inbound_member_email"}]
        result = main._filter_own_retry_entries(history, "retry-key")
        self.assertEqual(result, history)

    def test_does_not_strip_a_different_key_that_merely_shares_a_prefix(self):
        """'retry-key' must not accidentally match 'retry-key-2:...' — the match is on the
        exact "<key>:" prefix, not a raw substring."""
        history = [{"entry_id": "retry-key-2:inbound", "entry_type": "inbound_member_email"}]
        result = main._filter_own_retry_entries(history, "retry-key")
        self.assertEqual(result, history)

    def test_empty_history_returns_empty(self):
        self.assertEqual(main._filter_own_retry_entries([], "retry-key"), [])

    def test_entry_with_no_entry_id_is_kept(self):
        history = [{"entry_type": "inbound_member_email", "content": "no id"}]
        self.assertEqual(main._filter_own_retry_entries(history, "retry-key"), history)


@unittest.skipUnless(_MAIN_AVAILABLE, "bedrock_agentcore/strands/mcp not installed")
class UpdateCaseFactsCallerTests(unittest.TestCase):
    """main._update_case_facts (the Runtime-side caller of the case_facts Gateway route) must
    forward the attachment's REAL status for 'valid'/'incomplete' — never coerce a status, and
    never forward 'wrong_form'/'unreadable'/'missing'/'not_applicable', which have no single
    form_id they can be meaningfully attributed to."""

    def _case_facts_sent_for(self, attachment):
        from models import CaseInfo

        captured = {}

        async def fake_call_tool(mcp, tool_name, arguments):
            captured["tool_name"] = tool_name
            captured["arguments"] = arguments
            return {"status": "facts_updated"}

        cases = CaseInfo(case_id="CASE-1")
        with patch.object(main.gateway, "call_tool", fake_call_tool):
            asyncio.run(
                main._update_case_facts(object(), cases, "death_benefit_nomination", attachment=attachment)
            )
        return captured["arguments"]["case_facts"]

    def test_valid_attachment_is_forwarded_as_valid(self):
        from models import AttachmentAssessment

        attachment = AttachmentAssessment(status="valid", form_id="BDBN-NOM-V1")
        case_facts = self._case_facts_sent_for(attachment)
        self.assertEqual(case_facts["forms_on_file"], {"BDBN-NOM-V1": "valid"})

    def test_incomplete_attachment_is_forwarded_as_incomplete_not_coerced_to_valid(self):
        from models import AttachmentAssessment

        attachment = AttachmentAssessment(status="incomplete", form_id="BDBN-NOM-V1")
        case_facts = self._case_facts_sent_for(attachment)
        self.assertEqual(case_facts["forms_on_file"], {"BDBN-NOM-V1": "incomplete"})

    def test_wrong_form_is_not_forwarded(self):
        from models import AttachmentAssessment

        attachment = AttachmentAssessment(status="wrong_form", form_id="FH-V1")
        case_facts = self._case_facts_sent_for(attachment)
        self.assertNotIn("forms_on_file", case_facts)

    def test_unreadable_is_not_forwarded(self):
        from models import AttachmentAssessment

        attachment = AttachmentAssessment(status="unreadable")
        case_facts = self._case_facts_sent_for(attachment)
        self.assertNotIn("forms_on_file", case_facts)

    def test_no_attachment_sends_only_last_intent_id(self):
        case_facts = self._case_facts_sent_for(None)
        self.assertNotIn("forms_on_file", case_facts)
        self.assertEqual(case_facts["last_intent_id"], "death_benefit_nomination")


if __name__ == "__main__":
    unittest.main()
