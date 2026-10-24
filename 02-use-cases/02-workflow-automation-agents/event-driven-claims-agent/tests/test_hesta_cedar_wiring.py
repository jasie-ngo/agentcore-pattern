"""Tests for the Cedar rule-pack wiring in app/04_hesta_member_email/hesta/factory.py.

Offline: no AWS, no LLM.  The factory's ``Agent`` is replaced so we can see exactly what it would have been built
with.  Needs ``strands-agents[cedar]``, ``strands-compose`` and ``bedrock-agentcore`` installed (the factory imports
the Gateway client); the tests skip with a reason if they are not.

Run:
    python3 -m unittest tests.test_hesta_cedar_wiring -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "app" / "04_hesta_member_email",):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

SKIP_REASON = ""
try:
    import cedarpy  # noqa: F401, E402
    from core import cedar_pack, factory as core_factory  # noqa: E402
    from hesta import factory  # noqa: E402
    from strands import tool  # noqa: E402
    from strands.interventions.actions import Deny, Proceed  # noqa: E402
    from strands.models import Model  # noqa: E402
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    SKIP_REASON = f"needs strands-agents[cedar], strands-compose and bedrock-agentcore ({exc})"

CREATE_OK = {"status": "pending_review", "decision": "escalated"}
CREATE_BAD = {"status": "approved", "decision": "approved"}

# The pack under test, and its tool names as the agents see them (read from the pack's own vocabulary).
SOURCE_PACK = ROOT / "rules" / "packs" / "hesta-tool-boundary"
ACTION: dict = {}
if not SKIP_REASON:
    ACTION = {
        name: tool.action
        for name, tool in cedar_pack.parse_vocabulary((SOURCE_PACK / "vocabulary.yaml").read_text(encoding="utf-8")).tools.items()
    }


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class CedarWiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(core_factory, "Agent")
        self.Agent = patcher.start()
        self.addCleanup(patcher.stop)

    def _pack(self) -> Path:
        # An approved pack: the approval is a TEST FIXTURE inside a temp directory (see cedar_pack._approve_fixture).
        return cedar_pack._fresh_pack(self._tmp.name, "pack", SOURCE_PACK)

    def _kwargs(self) -> dict:
        return self.Agent.call_args.kwargs

    def test_off_by_default(self):
        factory.hesta_agent(name="human_handoff")
        self.assertNotIn("interventions", self._kwargs())

    def test_empty_value_is_off(self):
        # `cedar_pack: ${CEDAR_PACK_DIR:-}` resolves to "" when the variable is unset.
        factory.hesta_agent(name="human_handoff", cedar_pack="")
        self.assertNotIn("interventions", self._kwargs())

    def test_verified_pack_attaches_a_handler_that_uses_the_agent_as_principal(self):
        factory.hesta_agent(name="human_handoff", cedar_pack=str(self._pack()))
        handlers = self._kwargs()["interventions"]
        self.assertEqual(len(handlers), 1)
        handler = handlers[0]

        # No principal in invocation_state: the agent's own name is used, so the call is judged on its merits.
        self.assertIsInstance(handler.before_tool_call(cedar_pack._event({}, ACTION["create_claim"], CREATE_OK)), Proceed)
        self.assertIsInstance(handler.before_tool_call(cedar_pack._event({}, ACTION["create_claim"], CREATE_BAD)), Deny)
        self.assertIsInstance(handler.before_tool_call(cedar_pack._event({}, ACTION["send_notification"], {})), Deny)
        self.assertIsInstance(handler.before_tool_call(cedar_pack._event({}, ACTION["resolve_claim"], {})), Deny)

    def test_handler_denies_when_evaluation_fails(self):
        factory.hesta_agent(name="human_handoff", cedar_pack=str(self._pack()))
        self.assertEqual(self._kwargs()["interventions"][0].on_error, "deny")

    def test_unverifiable_pack_stops_the_agent_being_built(self):
        pack = self._pack()
        manifest_path = pack / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["approvals"] = []
        manifest_path.write_text(json.dumps(manifest))

        with self.assertRaises(cedar_pack.PackRejected):
            factory.hesta_agent(name="human_handoff", cedar_pack=str(pack))
        self.Agent.assert_not_called()

    def test_tampered_policy_stops_the_agent_being_built(self):
        pack = self._pack()
        with (pack / cedar_pack.POLICY_FILE).open("a", encoding="utf-8") as f:
            f.write("permit (principal, action, resource);\n")

        with self.assertRaises(cedar_pack.PackRejected):
            factory.hesta_agent(name="human_handoff", cedar_pack=str(pack))
        self.Agent.assert_not_called()

    def test_existing_interventions_are_kept(self):
        sentinel = object()
        factory.hesta_agent(name="human_handoff", cedar_pack=str(self._pack()), interventions=[sentinel])
        handlers = self._kwargs()["interventions"]
        self.assertIs(handlers[0], sentinel)
        self.assertEqual(len(handlers), 2)

    def test_the_agent_name_is_required(self):
        with self.assertRaises(ValueError):
            factory.hesta_agent(cedar_pack=str(self._pack()))
        self.Agent.assert_not_called()

    def test_missing_cedar_dependencies_fail_loudly(self):
        # The loader ships with the app now, so the thing that can be absent is the optional cedar extra.
        with mock.patch.object(cedar_pack, "load_cedar_authorization", side_effect=ImportError("no cedar")):
            with self.assertRaisesRegex(RuntimeError, "cedar.*extra"):
                factory.hesta_agent(name="human_handoff", cedar_pack=str(self._pack()))
        self.Agent.assert_not_called()


if not SKIP_REASON:

    class _ScriptedModel(Model):
        """Turn 1 asks for one tool call; turn 2 says it is done.  No network, no LLM."""

        def __init__(self, tool_name: str, args: dict):
            self.tool_name, self.args, self.turn = tool_name, args, 0

        def update_config(self, **kwargs):
            pass

        def get_config(self):
            return {}

        async def structured_output(self, *args, **kwargs):
            raise NotImplementedError

        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.turn += 1
            yield {"messageStart": {"role": "assistant"}}
            if self.turn == 1:
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "t1", "name": self.tool_name}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(self.args)}}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "tool_use"}}
            else:
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": "done"}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "end_turn"}}
            yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class RealAgentLoopTests(unittest.TestCase):
    """A real strands Agent built by the factory, a scripted model, and a stand-in tool that records if its body ran."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _run(self, short_name: str, args: dict, *, with_pack: bool) -> tuple[bool, str]:
        gateway_name = ACTION[short_name]
        ran: list = []

        @tool(name=gateway_name)
        def stand_in(status: str = "", decision: str = "", claim_id: str = "", to: str = "") -> str:
            """Stand-in for a Gateway tool."""
            ran.append(True)
            return "ok"

        extra = {"cedar_pack": str(cedar_pack._fresh_pack(self._tmp.name, f"p{len(ran)}-{short_name}", SOURCE_PACK))} if with_pack else {}
        agent = factory.hesta_agent(
            name="human_handoff", model=_ScriptedModel(gateway_name, args), tools=[stand_in],
            system_prompt="test", callback_handler=None, **extra,
        )
        agent("record this case")
        results = [b["toolResult"] for m in agent.messages for b in m.get("content", []) if "toolResult" in b]
        return bool(ran), results[-1]["content"][0]["text"] if results else ""

    def test_an_allowed_call_runs_its_tool(self):
        ran, _ = self._run("create_claim", CREATE_OK, with_pack=True)
        self.assertTrue(ran)

    def test_a_denied_call_never_runs_its_tool_and_the_model_is_told(self):
        for short, args in (("create_claim", CREATE_BAD), ("send_notification", {"to": "a@example.com"}), ("resolve_claim", {"claim_id": "C1"})):
            with self.subTest(tool=short):
                ran, text = self._run(short, args, with_pack=True)
                self.assertFalse(ran)
                self.assertIn("DENIED", text)

    def test_without_the_pack_the_same_call_does_run(self):
        # The control: it is the handler, not the test fixture, that stops the call.
        ran, _ = self._run("create_claim", CREATE_BAD, with_pack=False)
        self.assertTrue(ran)


if __name__ == "__main__":
    unittest.main()
