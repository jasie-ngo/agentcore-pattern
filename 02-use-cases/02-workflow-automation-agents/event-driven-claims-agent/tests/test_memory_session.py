"""Unit tests for memory/session.py's case-conversation store (TODO 3), with a fake
AgentCore Memory client — no AWS credentials needed.

Run:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "app", "hesta-claimsagent"))

try:
    import memory.session as session  # noqa: E402

    _MEMORY_MODULE_AVAILABLE = True
except ImportError:
    session = None
    _MEMORY_MODULE_AVAILABLE = False


class _FakeMemoryClient:
    """Mimics the subset of bedrock_agentcore.memory.MemoryClient that session.py uses.

    Truncates ``event_timestamp`` to millisecond precision on every call — real botocore
    serializes the timestamp it sends to the service rounded to milliseconds, so a fix that
    only survives microsecond-level spacing would pass against a naive fake but still be
    broken in production. Mirroring the truncation here is what makes these tests trustworthy.
    """

    def __init__(self, *, region_name=None):
        self.events: list[dict] = []
        self.create_event_calls = 0

    def create_event(self, *, memory_id, actor_id, session_id, messages, event_timestamp=None, metadata=None, branch=None):
        self.create_event_calls += 1
        ts = event_timestamp or (datetime.now(timezone.utc) + timedelta(milliseconds=len(self.events)))
        ts = ts.replace(microsecond=(ts.microsecond // 1000) * 1000)  # botocore: millisecond precision only
        payload = [{"conversational": {"content": {"text": text}, "role": role}} for text, role in messages]
        event = {
            "eventId": f"evt-{len(self.events)}",
            "eventTimestamp": ts,
            "payload": payload,
            "metadata": metadata or {},
        }
        self.events.append(event)
        return event

    def list_events(self, *, memory_id, actor_id, session_id, max_results=100, include_payload=True, **kwargs):
        # Mirrors the real ListEvents API: most-recent-first, so capping at max_results keeps
        # the NEWEST events — load_thread re-sorts to oldest-first itself afterward.
        events = sorted(self.events, key=lambda e: e["eventTimestamp"], reverse=True)
        if not include_payload:
            events = [{k: v for k, v in e.items() if k != "payload"} for e in events]
        return events[:max_results]


def _entries():
    return [
        {
            "entry_id": "key1:inbound",
            "entry_type": "inbound_member_email",
            "content": "Please send my BDBN form.",
            "timestamp": "2026-09-01T00:00:00+00:00",
            "intent_id": "death_benefit_nomination",
        },
        {
            "entry_id": "key1:draft",
            "entry_type": "generated_draft",
            "content": '{"subject": "RE: x", "body": "..."}',
            "timestamp": "2026-09-01T00:00:01+00:00",
            "intent_id": "death_benefit_nomination",
        },
        {
            "entry_id": "key1:review",
            "entry_type": "review_outcome",
            "content": '{"approved_for_human_send": true}',
            "timestamp": "2026-09-01T00:00:02+00:00",
            "intent_id": "death_benefit_nomination",
        },
    ]


@unittest.skipUnless(_MEMORY_MODULE_AVAILABLE, "bedrock_agentcore not installed")
class SafeMemoryIdTests(unittest.TestCase):
    def test_sanitizes_disallowed_characters(self):
        self.assertEqual(session.safe_memory_id("sarah@example.com"), "sarah-example-com")

    def test_guarantees_alphanumeric_first_character(self):
        result = session.safe_memory_id("---")
        self.assertTrue(result[0].isalnum())

    def test_blank_falls_back(self):
        self.assertTrue(session.safe_memory_id("").startswith("anonymous"))

    def test_thread_session_id_is_stable_for_the_same_case(self):
        self.assertEqual(session.thread_session_id("CASE-ABC123"), session.thread_session_id("CASE-ABC123"))

    def test_thread_session_id_differs_for_different_cases(self):
        self.assertNotEqual(session.thread_session_id("CASE-ABC123"), session.thread_session_id("CASE-XYZ789"))


@unittest.skipUnless(_MEMORY_MODULE_AVAILABLE, "bedrock_agentcore not installed")
class MemoryDisabledTests(unittest.TestCase):
    """MEMORY_ID unset -> helpers return empty/False without raising or touching the client."""

    def test_append_turns_returns_false_when_unconfigured(self):
        with patch.object(session, "MEMORY_ID", ""):
            self.assertFalse(session.append_turns("actor-1", "session-1", _entries()))

    def test_load_thread_returns_empty_when_unconfigured(self):
        with patch.object(session, "MEMORY_ID", ""):
            self.assertEqual(session.load_thread("actor-1", "session-1"), [])

    def test_append_turns_with_no_entries_is_a_noop(self):
        with patch.object(session, "MEMORY_ID", "mem-123"):
            self.assertFalse(session.append_turns("actor-1", "session-1", []))


@unittest.skipUnless(_MEMORY_MODULE_AVAILABLE, "bedrock_agentcore not installed")
class AppendAndLoadTests(unittest.TestCase):
    def setUp(self):
        self.fake_client = _FakeMemoryClient()
        self._memory_id_patch = patch.object(session, "MEMORY_ID", "mem-123")
        self._memory_id_patch.start()
        self._client_patch = patch("bedrock_agentcore.memory.MemoryClient", return_value=self.fake_client)
        self._client_patch.start()

    def tearDown(self):
        self._client_patch.stop()
        self._memory_id_patch.stop()

    def test_appends_one_event_per_entry_with_the_right_roles(self):
        ok = session.append_turns("actor-1", "session-1", _entries())
        self.assertTrue(ok)
        self.assertEqual(len(self.fake_client.events), 3)
        roles = [e["payload"][0]["conversational"]["role"] for e in self.fake_client.events]
        self.assertEqual(roles, ["USER", "ASSISTANT", "OTHER"])

    def test_load_thread_returns_entries_oldest_first_in_the_old_dict_shape(self):
        session.append_turns("actor-1", "session-1", _entries())
        turns = session.load_thread("actor-1", "session-1")
        self.assertEqual(len(turns), 3)
        self.assertEqual([t["entry_id"] for t in turns], ["key1:inbound", "key1:draft", "key1:review"])
        self.assertEqual(turns[0]["entry_type"], "inbound_member_email")
        self.assertEqual(turns[0]["content"], "Please send my BDBN form.")
        self.assertEqual(turns[0]["intent_id"], "death_benefit_nomination")

    def test_same_call_entries_get_strictly_increasing_timestamps_even_on_a_tied_wall_clock(self):
        """Regression: create_event's own wall-clock default can tie for events issued in the
        same tight loop, which would make load_thread's timestamp-only sort return them in an
        unpredictable order. append_turns must space them out itself."""
        fixed_now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        with patch("memory.session.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            session.append_turns("actor-1", "session-1", _entries())

        timestamps = [e["eventTimestamp"] for e in self.fake_client.events]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(len(set(timestamps)), 3, "each event in the same call must get a distinct timestamp")

        turns = session.load_thread("actor-1", "session-1")
        self.assertEqual([t["entry_id"] for t in turns], ["key1:inbound", "key1:draft", "key1:review"])

    def test_load_thread_breaks_a_genuine_timestamp_tie_using_seq(self):
        """Direct proof of the (timestamp, seq) sort key: even if the timestamp itself ties —
        e.g. the service rounds coarser than milliseconds, or a future change to append_turns's
        spacing regresses — events from the same call must still come back in write order,
        using the 'seq' metadata append_turns stamps on each one."""
        tied_ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for entry_id, seq in [("key1:review", "2"), ("key1:inbound", "0"), ("key1:draft", "1")]:
            self.fake_client.events.append(
                {
                    "eventId": f"evt-{entry_id}",
                    "eventTimestamp": tied_ts,
                    "payload": [{"conversational": {"content": {"text": entry_id}, "role": "USER"}}],
                    "metadata": {"entry_id": {"stringValue": entry_id}, "seq": {"stringValue": seq}},
                }
            )

        turns = session.load_thread("actor-1", "session-1")
        self.assertEqual([t["entry_id"] for t in turns], ["key1:inbound", "key1:draft", "key1:review"])

    def test_reprocessing_the_same_entries_does_not_duplicate(self):
        session.append_turns("actor-1", "session-1", _entries())
        first_count = self.fake_client.create_event_calls
        session.append_turns("actor-1", "session-1", _entries())
        self.assertEqual(self.fake_client.create_event_calls, first_count)
        self.assertEqual(len(self.fake_client.events), 3)

    def test_a_new_entry_alongside_known_ones_is_appended_once(self):
        session.append_turns("actor-1", "session-1", _entries()[:2])
        session.append_turns("actor-1", "session-1", _entries())
        self.assertEqual(len(self.fake_client.events), 3)

    def test_two_emails_on_the_same_case_share_one_session(self):
        session.append_turns("actor-1", "session-1", _entries()[:1])
        session.append_turns(
            "actor-1",
            "session-1",
            [
                {
                    "entry_id": "key2:inbound",
                    "entry_type": "inbound_member_email",
                    "content": "Following up on my form.",
                    "timestamp": "2026-09-05T00:00:00+00:00",
                    "intent_id": "death_benefit_nomination",
                }
            ],
        )
        turns = session.load_thread("actor-1", "session-1")
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[-1]["entry_id"], "key2:inbound")

    def test_inbound_carries_attachment_metadata(self):
        entries = [
            {
                "entry_id": "key1:inbound",
                "entry_type": "inbound_member_email",
                "content": "Please find attached my form.",
                "timestamp": "2026-09-01T00:00:00+00:00",
                "intent_id": "death_benefit_nomination",
                "attachment_status": "incomplete",
                "form_id": "BDBN-NOM-V1",
                "missing_fields": ["Nomination details"],
            }
        ]
        session.append_turns("actor-1", "session-1", entries)
        turns = session.load_thread("actor-1", "session-1")
        self.assertEqual(turns[0]["attachment_status"], "incomplete")
        self.assertEqual(turns[0]["form_id"], "BDBN-NOM-V1")
        self.assertEqual(turns[0]["missing_fields"], ["Nomination details"])

    def test_load_thread_caps_at_max_turns(self):
        many = [
            {
                "entry_id": f"k{i}:inbound",
                "entry_type": "inbound_member_email",
                "content": f"msg {i}",
                "timestamp": f"2026-09-01T00:00:{i:02d}+00:00",
            }
            for i in range(5)
        ]
        session.append_turns("actor-1", "session-1", many)
        turns = session.load_thread("actor-1", "session-1", max_turns=2)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[-1]["entry_id"], "k4:inbound")


@unittest.skipUnless(_MEMORY_MODULE_AVAILABLE, "bedrock_agentcore not installed")
class LoadFailureIsVisibleTests(unittest.TestCase):
    """A configured Memory that fails to load must raise, not silently return [] indistinguishable
    from an empty session (implementation rule 4 — the caller surfaces this distinctly)."""

    def test_load_thread_raises_on_a_configured_client_failure(self):
        class _BrokenClient:
            def __init__(self, *, region_name=None):
                pass

            def list_events(self, **kwargs):
                raise RuntimeError("service unavailable")

        with patch.object(session, "MEMORY_ID", "mem-123"), patch(
            "bedrock_agentcore.memory.MemoryClient", return_value=_BrokenClient()
        ):
            with self.assertRaises(RuntimeError):
                session.load_thread("actor-1", "session-1")

    def test_append_turns_never_raises_on_a_client_failure(self):
        class _BrokenClient:
            def __init__(self, *, region_name=None):
                pass

            def list_events(self, **kwargs):
                return []

            def create_event(self, **kwargs):
                raise RuntimeError("service unavailable")

        with patch.object(session, "MEMORY_ID", "mem-123"), patch(
            "bedrock_agentcore.memory.MemoryClient", return_value=_BrokenClient()
        ):
            self.assertFalse(session.append_turns("actor-1", "session-1", _entries()))


if __name__ == "__main__":
    unittest.main()
