"""Tests for per-agent scoping in core/cedar_pack.py.

A rule may say `for the agent "A"`.  The agent names are listed in the vocabulary's `agents:` section, so a misspelt or
invented agent is refused when the pack is built.  Each agent is its own Cedar principal, `Agent::"<name>"`, which is how
the factory builds it; a statement without `for the agent` still applies to every agent carrying the pack.

The vocabulary below is SYNTHETIC: invented for these tests, and not any real organisation's rules.

Run:
    python3 -m unittest tests.test_cedar_pack_agent_scope -v

Needs PyYAML.  The handler and agent-loop tests also need `strands-agents[cedar]` and skip with a reason without it.
The agent-loop tests reuse demo/run_demo.py's scripted model and stand-in tool, so there is one copy of those.
"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "app" / "04_hesta_member_email", ROOT / "demo"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from core import cedar_pack  # noqa: E402

HANDLER_SKIP = ""
try:
    import cedarpy  # noqa: F401, E402
    import run_demo  # noqa: E402
    from strands.vended_interventions.cedar import CedarAuthorization  # noqa: F401, E402
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    HANDLER_SKIP = f"needs strands-agents[cedar] ({exc})"

VOCABULARY = textwrap.dedent("""\
    pack_id: example-multi-agent
    jurisdiction: AU
    agents:
      reader:  { description: "Reads member records" }
      handoff: { description: "Hands cases to staff" }
    tools:
      lookup_member:
        action: records___lookup_member
        description: Reads a member record.
        args:
          member_id: { description: The member }
      open_case:
        action: cases___open_case
        description: Opens a case.
        args:
          status:
            description: The status of the new case.
            values: [pending_review, closed]
      classify:
        action: classify_locally
        description: A local tool, not a Gateway tool.
        args: {}
    """)

FEATURE = textwrap.dedent("""\
    Feature: Example multi-agent tool boundary

      Rule: Only the reader reads records and classifies
        @policy
        Scenario: Policy
          Given the tool "lookup_member" is permitted for the agent "reader"
          And the tool "classify" is permitted for the agent "reader"

        @must
        Scenario Outline: Who may read
          When the agent "<agent>" calls "<tool>"
          Then the call is <outcome>

          Examples:
            | agent   | tool          | outcome |
            | reader  | lookup_member | allowed |
            | reader  | classify      | allowed |
            | handoff | lookup_member | denied  |
            | handoff | classify      | denied  |

      Rule: Only the handoff agent opens cases, and only for review
        @policy
        Scenario: Policy
          Given the tool "open_case" is permitted for the agent "handoff" only when status is "pending_review"

        @must
        Scenario Outline: Who may open a case
          When the agent "<agent>" calls "open_case" with status "<status>"
          Then the call is <outcome>

          Examples:
            | agent   | status         | outcome |
            | handoff | pending_review | allowed |
            | handoff | closed         | denied  |
            | reader  | pending_review | denied  |
    """)


def _vocab(text: str = VOCABULARY):
    return cedar_pack.parse_vocabulary(text)


def _compile(feature: str, vocab=None) -> str:
    return cedar_pack.compile_policy(feature, vocab or _vocab(), feature_name="t.feature", vocabulary_sha="0" * 64)


def _feature_with(line: str) -> str:
    return f"Feature: t\n  Rule: r\n    @policy\n    Scenario: Policy\n      Given {line}\n"


def _swap(text: str, old: str, new: str) -> str:
    assert old in text, f"the mutation target {old!r} is not in the text, so the test would pass for no reason"
    return text.replace(old, new)


class VocabularyAgentsTests(unittest.TestCase):
    def test_agents_are_read(self):
        self.assertEqual(set(_vocab().agents), {"reader", "handoff"})

    def test_a_vocabulary_with_no_agents_section_still_parses(self):
        without = VOCABULARY.split("agents:")[0] + "tools:" + VOCABULARY.split("tools:", 1)[1]
        self.assertEqual(_vocab(without).agents, {})

    def test_a_misspelt_agent_key_is_refused(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "unknown key"):
            _vocab(_swap(VOCABULARY, 'description: "Reads member records"', 'descripton: "Reads member records"'))

    def test_an_agent_name_must_be_a_plain_identifier(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "agent name"):
            _vocab(_swap(VOCABULARY, "  reader:  {", '  "re ader": {'))

    def test_agents_must_be_a_mapping(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "mapping"):
            _vocab(_swap(VOCABULARY, 'agents:\n  reader:  { description: "Reads member records" }\n  handoff: { description: "Hands cases to staff" }\n', "agents: [reader, handoff]\n"))


class CompileScopedTests(unittest.TestCase):
    def test_a_scoped_permit_names_one_principal(self):
        policy = _compile(_feature_with('the tool "lookup_member" is permitted for the agent "reader"'))
        self.assertIn('permit (principal == Agent::"reader", action == Action::"records___lookup_member", resource);', policy)

    def test_a_scoped_never_names_one_principal(self):
        policy = _compile(_feature_with('the tool "open_case" is never permitted for the agent "reader"'))
        self.assertIn('forbid (principal == Agent::"reader", action == Action::"cases___open_case", resource);', policy)

    def test_a_scoped_condition_keeps_both_the_principal_and_the_condition(self):
        policy = _compile(_feature_with('the tool "open_case" is permitted for the agent "handoff" only when status is "pending_review"'))
        self.assertIn('permit (principal == Agent::"handoff", action == Action::"cases___open_case", resource)', policy)
        self.assertIn('context.input.status == "pending_review"', policy)

    def test_an_unscoped_statement_compiles_exactly_as_before(self):
        policy = _compile(_feature_with('the tool "lookup_member" is permitted'))
        self.assertIn('permit (principal, action == Action::"records___lookup_member", resource);', policy)
        self.assertNotIn("Agent::", policy)

    def test_an_unknown_agent_is_refused_and_the_known_ones_are_listed(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "unknown agent 'writer'.*handoff, reader"):
            _compile(_feature_with('the tool "lookup_member" is permitted for the agent "writer"'))

    def test_a_vocabulary_with_no_agents_refuses_a_scoped_rule(self):
        without = VOCABULARY.split("agents:")[0] + "tools:" + VOCABULARY.split("tools:", 1)[1]
        with self.assertRaisesRegex(cedar_pack.CompileError, "lists no agents"):
            _compile(_feature_with('the tool "lookup_member" is permitted for the agent "reader"'), _vocab(without))

    def test_the_agent_must_come_before_the_condition(self):
        line = 'the tool "open_case" is permitted only when status is "pending_review" for the agent "handoff"'
        with self.assertRaisesRegex(cedar_pack.CompileError, "cannot read the conditions|not in the policy vocabulary"):
            _compile(_feature_with(line))

    def test_the_checked_in_packs_still_compile_to_the_same_bytes(self):
        # Scoping must not disturb a pack that does not use it: an approval of one of them would still hold.
        for name in ("hesta-tool-boundary", "health-insurer-illustrative"):
            source = ROOT / "rules" / "packs" / name
            with self.subTest(pack=name), tempfile.TemporaryDirectory() as tmp:
                pack = Path(tmp) / "p"
                pack.mkdir()
                for f in list(source.glob("*.feature")) + [source / "vocabulary.yaml"]:
                    (pack / f.name).write_bytes(f.read_bytes())
                cedar_pack.build(pack)
                self.assertEqual((pack / cedar_pack.POLICY_FILE).read_bytes(), (source / cedar_pack.POLICY_FILE).read_bytes())


def _approved(tmp: str, feature: str = FEATURE, vocabulary: str = VOCABULARY) -> Path:
    source = Path(tmp) / "source"
    source.mkdir()
    (source / "multi.feature").write_text(feature, encoding="utf-8")
    (source / "vocabulary.yaml").write_text(vocabulary, encoding="utf-8")
    return cedar_pack._fresh_pack(tmp, "approved", source)


@unittest.skipIf(HANDLER_SKIP, HANDLER_SKIP)
class HandlerScopedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _run(self, scenario_text: str, pack: Path):
        handler = cedar_pack.load_cedar_authorization(pack)
        scenario = [s for s in cedar_pack.parse_feature(scenario_text) if "must" in s.tags][0]
        return cedar_pack._run_must(handler, _vocab(), scenario)

    def test_every_scenario_passes_through_the_real_handler(self):
        pack = _approved(self._tmp.name)
        handler = cedar_pack.load_cedar_authorization(pack)
        scenarios = [s for s in cedar_pack.parse_feature(FEATURE) if "must" in s.tags]
        self.assertEqual(len(scenarios), 7)  # 4 + 3 expanded rows
        for scenario in scenarios:
            with self.subTest(scenario=scenario.name):
                ok, why = cedar_pack._run_must(handler, _vocab(), scenario)
                self.assertTrue(ok, why)

    def test_a_wrong_expectation_is_caught(self):
        # The runner must be able to fail: the handoff agent is NOT allowed to read records.
        wrong = 'Feature: t\n  @must\n  Scenario: wrong\n    When the agent "handoff" calls "lookup_member"\n    Then the call is allowed\n'
        ok, why = self._run(wrong, _approved(self._tmp.name))
        self.assertFalse(ok)
        self.assertIn("expected allowed, got denied", why)

    def test_a_scenario_naming_an_unlisted_agent_fails(self):
        scenario = 'Feature: t\n  @must\n  Scenario: x\n    When the agent "stranger" calls "lookup_member"\n    Then the call is denied\n'
        ok, why = self._run(scenario, _approved(self._tmp.name))
        self.assertFalse(ok)
        self.assertIn("not in the vocabulary", why)

    def test_an_unscoped_permit_applies_to_every_agent_but_a_scoped_never_removes_one(self):
        feature = _feature_with('the tool "lookup_member" is permitted') + '      And the tool "lookup_member" is never permitted for the agent "handoff"\n'
        pack = _approved(self._tmp.name, feature=feature)
        handler = cedar_pack.load_cedar_authorization(pack)
        for who, want in (("reader", True), ("handoff", False)):
            with self.subTest(agent=who):
                scenario = f'Feature: t\n  @must\n  Scenario: s\n    When the agent "{who}" calls "lookup_member"\n    Then the call is {"allowed" if want else "denied"}\n'
                ok, why = cedar_pack._run_must(handler, _vocab(), [s for s in cedar_pack.parse_feature(scenario) if "must" in s.tags][0])
                self.assertTrue(ok, why)


@unittest.skipIf(HANDLER_SKIP, HANDLER_SKIP)
class RealAgentLoopScopedTests(unittest.TestCase):
    """The same approved pack carried by two real Strands agents. Each is judged as itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pack = _approved(self._tmp.name)
        self.vocab = _vocab()

    def _call(self, agent: str, tool: str, args: dict):
        return run_demo.agent_call(self.pack, agent, self.vocab, tool, args)

    def test_the_same_pack_lets_one_agent_in_and_keeps_another_out(self):
        ran, _ = self._call("reader", "lookup_member", {"member_id": "M-1"})
        self.assertTrue(ran)
        ran, told = self._call("handoff", "lookup_member", {"member_id": "M-1"})
        self.assertFalse(ran)
        self.assertIn("DENIED", told)

    def test_the_handoff_agent_may_open_only_a_pending_review_case(self):
        self.assertTrue(self._call("handoff", "open_case", {"status": "pending_review"})[0])
        self.assertFalse(self._call("handoff", "open_case", {"status": "closed"})[0])
        self.assertFalse(self._call("reader", "open_case", {"status": "pending_review"})[0])

    def test_a_local_tool_is_governed_like_any_other(self):
        # A pack carried by an agent judges ALL of that agent's tool calls, local ones included, so a local tool
        # has to be listed in the vocabulary (its `action` is the local tool's name) or it is refused.
        self.assertTrue(self._call("reader", "classify", {})[0])
        self.assertFalse(self._call("handoff", "classify", {})[0])


if __name__ == "__main__":
    unittest.main()
