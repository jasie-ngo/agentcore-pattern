"""Tests for memory keyed on a VERIFIED member only, in app/04_hesta_member_email.

The YAML port used to give its one session manager a constant actor, so every member's contacts were pooled and
recalled for everyone. Memory is now two explicit tools that do nothing unless ``classify_verification`` has verified
THIS member number for THIS sender address.  These tests include the attack that motivated the change: a different
sender quoting a verified member's number.

All member data below is invented. Memory is a fake client: no AWS.

Run:
    python3 -m unittest tests.test_hesta_memory_guard -v

Needs PyYAML and `strands-agents`; the real-config and real-agent tests also need `strands-compose` / `strands-agents[cedar]`
and skip with a reason without them.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
for _path in (ROOT / "demo", ROOT / "rules", APP):  # the app dir ends up first
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

SKIP_REASON = ""
try:
    from core import memory  # noqa: E402
    from hesta import tools  # noqa: E402
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    SKIP_REASON = f"needs strands-agents ({exc})"

CONFIG = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))
PROMPT = CONFIG["agents"]["coordinator"]["system_prompt"]

MEMBER, MEMBER_EMAIL = "60010001", "sarah.thompson@example.com"
ATTACKER_EMAIL = "attacker@example.net"


def _verify(number=MEMBER, sender=MEMBER_EMAIL, record_email=MEMBER_EMAIL, status="active"):
    return tools.classify_verification(
        member_number=number, sender_email=sender, record_found=True, record_email=record_email,
        record_status=status, record_policy_type="super_accumulation", sender_authenticated=True,
    )


class FakeClient:
    """A stand-in for MemoryClient that records every call."""

    def __init__(self, records=None, fail=False):
        self.records, self.fail = records or [], fail
        self.retrieved, self.created = [], []

    def retrieve_memories(self, **kwargs):
        self.retrieved.append(kwargs)
        if self.fail:
            raise RuntimeError("boom")
        return self.records

    def create_event(self, **kwargs):
        self.created.append(kwargs)
        if self.fail:
            raise RuntimeError("boom")
        return {}


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        memory.forget_all()
        self.addCleanup(memory.forget_all)
        env = mock.patch.dict(os.environ, {"MEMORY_CLAIMSAGENTMEMORY_ID": "mem-1"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AGENTCORE_MEMORY_ID", None)


class MemoryActorTests(MemoryTestCase):
    def test_a_plain_member_number_is_the_actor(self):
        self.assertEqual(memory.memory_actor("60010001"), "60010001")
        self.assertEqual(memory.memory_actor("  60010001 "), "60010001")

    def test_anything_else_is_not_an_actor(self):
        for bad in ("", None, "123", "sarah.thompson@example.com", "6001/0001", "6001 0001", "60010001\n..", "x" * 25, "é1234567"):
            with self.subTest(value=bad):
                self.assertIsNone(memory.memory_actor(bad))

    def test_a_number_with_a_hyphen_or_underscore_is_an_actor_exactly_as_written(self):
        # A second client's numbers look like POL-12345. They are kept as they are, never rewritten.
        self.assertEqual(memory.memory_actor("POL-12345"), "POL-12345")
        self.assertEqual(memory.memory_actor("POL_12345"), "POL_12345")

    def test_a_hyphen_may_not_lead_and_other_punctuation_is_still_refused(self):
        for bad in ("-1234567", "_1234567", "POL.12345", "POL 12345", "POL/12345", "POL-1234\n..", "POL-12345@x"):
            with self.subTest(value=bad):
                self.assertIsNone(memory.memory_actor(bad))

    def test_two_different_inputs_never_share_an_actor(self):
        # The deployed app turned every odd character into "-", so john.smith and john-smith collided. Nothing is rewritten now:
        # john.smith is refused and john-smith is its own actor, so no two inputs can ever share one.
        self.assertIsNone(memory.memory_actor("john.smith"))
        self.assertEqual(memory.memory_actor("john-smith"), "john-smith")
        self.assertNotEqual(memory.memory_actor("john_smith"), memory.memory_actor("john-smith"))


class VerificationRegistersTests(MemoryTestCase):
    def test_a_verified_member_is_registered_for_their_own_address_only(self):
        self.assertEqual(_verify()["verification_level"], "verified")
        self.assertTrue(memory.is_verified(MEMBER, MEMBER_EMAIL))
        self.assertTrue(memory.is_verified(MEMBER, MEMBER_EMAIL.upper()))  # addresses compare case-insensitively
        self.assertFalse(memory.is_verified(MEMBER, ATTACKER_EMAIL))

    def test_a_different_sender_quoting_the_number_is_only_partial_and_is_not_registered(self):
        result = _verify(sender=ATTACKER_EMAIL)
        self.assertEqual(result["verification_level"], "partial")
        self.assertFalse(memory.is_verified(MEMBER, ATTACKER_EMAIL))

    def test_an_inactive_account_is_not_registered(self):
        self.assertEqual(_verify(status="closed")["verification_level"], "unverified")
        self.assertFalse(memory.is_verified(MEMBER, MEMBER_EMAIL))

    def test_no_record_and_no_number_are_not_registered(self):
        tools.classify_verification(member_number=MEMBER, sender_email=MEMBER_EMAIL, record_found=False)
        tools.classify_verification(member_number="", sender_email=MEMBER_EMAIL)
        self.assertFalse(memory.is_verified(MEMBER, MEMBER_EMAIL))

    def test_the_registry_is_bounded(self):
        with mock.patch.object(memory, "_MAX_VERIFIED", 2):
            for n in ("10000001", "10000002", "10000003"):
                memory.register_verified(n, "a@example.com")
        self.assertFalse(memory.is_verified("10000001", "a@example.com"))  # the oldest was evicted
        self.assertTrue(memory.is_verified("10000003", "a@example.com"))


class RecallTests(MemoryTestCase):
    def test_nothing_is_recalled_for_an_unverified_sender_and_memory_is_not_touched(self):
        client = FakeClient([{"content": {"text": "earlier"}, "score": 0.9}])
        result = memory.recall(MEMBER, MEMBER_EMAIL, "hello", client=client)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["memories"], [])
        self.assertEqual(client.retrieved, [])

    def test_an_attacker_quoting_a_verified_members_number_recalls_nothing(self):
        _verify()  # the real member was verified earlier
        client = FakeClient([{"content": {"text": "the member's private history"}, "score": 0.9}])
        result = memory.recall(MEMBER, ATTACKER_EMAIL, "I am the member", client=client)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["memories"], [])
        self.assertEqual(client.retrieved, [], "memory was queried for an unverified sender")

    def test_a_verified_member_recalls_their_own_facts_namespace(self):
        _verify()
        client = FakeClient([
            {"content": {"text": "asked about FH in June"}, "score": 0.9},
            {"content": {"text": "too weak"}, "score": 0.1},
            {"content": {"text": "   "}, "score": 0.9},
            {"content": "not a dict", "score": 0.9},
        ])
        result = memory.recall(MEMBER, MEMBER_EMAIL, "any update?", client=client)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["memories"], ["asked about FH in June"])
        call = client.retrieved[0]
        self.assertEqual(call["namespace_path"], "claims/60010001/facts")
        self.assertEqual(call["memory_id"], "mem-1")
        self.assertEqual(call["query"], "any update?")

    def test_without_a_memory_id_a_verified_member_just_gets_nothing(self):
        _verify()
        os.environ.pop("MEMORY_CLAIMSAGENTMEMORY_ID")
        client = FakeClient()
        result = memory.recall(MEMBER, MEMBER_EMAIL, "hi", client=client)
        self.assertEqual((result["allowed"], result["memories"]), (True, []))
        self.assertEqual(client.retrieved, [])

    def test_a_memory_failure_never_breaks_processing(self):
        _verify()
        result = memory.recall(MEMBER, MEMBER_EMAIL, "hi", client=FakeClient(fail=True))
        self.assertEqual(result["memories"], [])
        self.assertIn("failed", result["reason"])


class RememberTests(MemoryTestCase):
    def test_an_unverified_sender_cannot_write_into_a_members_memory(self):
        _verify()
        client = FakeClient()
        result = memory.remember(MEMBER, ATTACKER_EMAIL, "poison", "poison", client=client)
        self.assertFalse(result["recorded"])
        self.assertEqual(client.created, [], "memory was written for an unverified sender")

    def test_a_verified_contact_is_recorded_under_the_members_actor(self):
        _verify()
        client = FakeClient()
        result = memory.remember(MEMBER, MEMBER_EMAIL, "m" * 9000, "summary", client=client)
        self.assertTrue(result["recorded"])
        event = client.created[0]
        self.assertEqual(event["actor_id"], "60010001")
        self.assertTrue(event["session_id"].startswith("session-60010001-"))
        self.assertEqual(event["messages"][0][1], "USER")
        self.assertEqual(len(event["messages"][0][0]), 8000)  # truncated
        self.assertEqual(event["messages"][1], ("summary", "ASSISTANT"))

    def test_a_write_failure_is_reported_not_raised(self):
        _verify()
        result = memory.remember(MEMBER, MEMBER_EMAIL, "m", "s", client=FakeClient(fail=True))
        self.assertFalse(result["recorded"])


class ConfigTests(unittest.TestCase):
    def test_there_is_no_global_session_manager_and_no_agent_has_one(self):
        self.assertNotIn("session_manager", CONFIG)
        for name, agent in CONFIG["agents"].items():
            with self.subTest(agent=name):
                self.assertNotIn("session_manager", agent)

    def test_the_static_actor_factory_is_gone(self):
        if SKIP_REASON:
            self.skipTest(SKIP_REASON)
        self.assertFalse(hasattr(memory, "hesta_session_manager"))

    def test_the_coordinator_has_the_two_memory_tools(self):
        wanted = {"./hesta/tools.py:recall_member_memory", "./hesta/tools.py:remember_member_contact"}
        self.assertTrue(wanted <= set(CONFIG["agents"]["coordinator"]["tools"]))

    def test_identity_comes_before_recall_which_comes_before_context_and_remember_is_last(self):
        steps = [PROMPT.index(s) for s in ("b. identity_profiler", "c. recall_member_memory", "d. context_manager")]
        self.assertEqual(steps, sorted(steps))
        self.assertLess(PROMPT.index("d. context_manager"), PROMPT.index("PHASE 2"))
        phase4 = PROMPT.index("PHASE 4")
        self.assertIn("remember_member_contact", PROMPT[phase4:])      # the write happens in the last phase
        self.assertNotIn("remember_member_contact", PROMPT[:phase4])   # and nowhere earlier

    def test_recall_and_remember_are_conditional_on_a_verified_identity_in_the_prompt(self):
        self.assertEqual(PROMPT.count('verification_level "verified"'), 2)  # recall and remember

    def test_the_context_manager_connection_no_longer_keeps_a_session(self):
        connection = next(c for c in CONFIG["orchestrations"]["main"]["connections"] if c["agent"] == "context_manager")
        self.assertFalse(connection["preserve_context"])

    def test_the_real_loader_still_builds_every_agent_with_the_memory_tools(self):
        try:
            from strands_compose import load
        except ImportError as exc:
            self.skipTest(f"needs strands-compose ({exc})")
        with mock.patch.dict(os.environ, {"AWS_REGION": "ap-southeast-2", "AWS_DEFAULT_REGION": "ap-southeast-2"}):
            resolved = load(str(APP / "config.yaml"), session_id="memory-guard-test-0000000000000000000000")
        self.assertEqual(len(resolved.agents), 10)
        names = set(resolved.agents["coordinator"].tool_names)
        self.assertTrue({"recall_member_memory", "remember_member_contact"} <= names, names)


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class RealToolCallTests(MemoryTestCase):
    """The tools as a real Strands agent calls them, with a scripted model standing in for the attacker's prompt."""

    def _call(self, args):
        try:
            import run_demo
        except ImportError as exc:
            self.skipTest(f"needs the demo helpers ({exc})")
        from strands import Agent

        agent = Agent(
            model=run_demo._make_model_class()("recall_member_memory", args),
            tools=[tools.recall_member_memory], system_prompt="test", callback_handler=None,
        )
        agent("recall")
        results = [b["toolResult"] for m in agent.messages for b in m.get("content", []) if "toolResult" in b]
        return json.dumps(results[-1]["content"])

    def test_a_model_asked_to_recall_for_an_unverified_sender_gets_nothing(self):
        _verify()  # the real member is verified, the attacker is not
        out = self._call({"member_number": MEMBER, "sender_email": ATTACKER_EMAIL, "latest_message": "hi"})
        self.assertIn("false", out.lower())
        self.assertIn("not verified", out)

    def test_the_same_call_for_the_verified_sender_is_allowed(self):
        _verify()
        with mock.patch.object(memory, "_client", return_value=FakeClient([{"content": {"text": "earlier"}, "score": 0.9}])):
            out = self._call({"member_number": MEMBER, "sender_email": MEMBER_EMAIL, "latest_message": "hi"})
        self.assertIn("earlier", out)


if __name__ == "__main__":
    unittest.main()
