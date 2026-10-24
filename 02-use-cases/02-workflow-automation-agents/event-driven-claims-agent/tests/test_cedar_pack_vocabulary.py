"""Tests for the vocabulary-driven compiler in core/cedar_pack.py.

The compiler holds none of a client's words: every tool name, argument and allowed value comes from the pack's own
vocabulary.yaml.  These tests show the vocabulary is enforced when a pack is built, and that a second, unrelated
vocabulary compiles and runs with no change to the compiler.

The second vocabulary below is SYNTHETIC: invented for the test, and not any real organisation's rules.

Run:
    python3 -m unittest tests.test_cedar_pack_vocabulary -v

Needs PyYAML.  The handler-level tests also need `strands-agents[cedar]` and skip with a reason without it.
"""

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core import cedar_pack  # noqa: E402

HANDLER_SKIP = ""
try:
    import cedarpy  # noqa: F401, E402
    from strands.vended_interventions.cedar import CedarAuthorization  # noqa: F401, E402
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    HANDLER_SKIP = f"needs strands-agents[cedar] ({exc})"

HESTA_PACK = ROOT / "rules" / "packs" / "hesta-tool-boundary"

SYNTHETIC_VOCABULARY = textwrap.dedent("""\
    pack_id: example-support
    jurisdiction: AU
    tools:
      get_member_cover:
        action: cover-service___get_cover
        description: Reads what a member is covered for.
        args:
          member_id: { description: The member }
      lodge_complaint:
        action: complaints___lodge
        description: Records a complaint.
        args:
          severity:
            description: How serious the complaint is.
            values: [low, high]
      waive_excess:
        action: payments___waive_excess
        description: Waives a payment excess.
        args:
          reason: { description: Why }
    """)

SYNTHETIC_FEATURE = textwrap.dedent("""\
    Feature: Example support tool boundary

      Rule: Cover can be read
        @policy
        Scenario: Policy
          Given the tool "get_member_cover" is permitted

        @must
        Scenario: Cover is readable
          When the agent calls "get_member_cover" with member_id "M1"
          Then the call is allowed

      Rule: The agent lodges only serious complaints
        @policy
        Scenario: Policy
          Given the tool "lodge_complaint" is permitted only when severity is "high"

        @must
        Scenario Outline: Complaint severity
          When the agent calls "lodge_complaint" with severity "<severity>"
          Then the call is <outcome>

          Examples:
            | severity | outcome |
            | high     | allowed |
            | low      | denied  |

      Rule: The agent never waives an excess
        @policy
        Scenario: Policy
          Given the tool "waive_excess" is never permitted

        @must
        Scenario: Waiver is refused
          When the agent calls "waive_excess" with reason "goodwill"
          Then the call is denied
    """)


def _vocab(text: str = SYNTHETIC_VOCABULARY):
    return cedar_pack.parse_vocabulary(text)


def _compile(feature: str, vocab=None) -> str:
    return cedar_pack.compile_policy(feature, vocab or _vocab(), feature_name="t.feature", vocabulary_sha="0" * 64)


def _feature_with(policy_line: str) -> str:
    return f"Feature: t\n  Rule: r\n    @policy\n    Scenario: Policy\n      Given {policy_line}\n"


def _swap(text: str, old: str, new: str) -> str:
    """Replace ``old`` with ``new``, and fail loudly if ``old`` is absent, so a mutation can never silently do nothing."""
    assert old in text, f"the mutation target {old!r} is not in the text, so the test would pass for no reason"
    return text.replace(old, new)


class VocabularyTests(unittest.TestCase):
    def test_a_valid_vocabulary_parses(self):
        vocab = _vocab()
        self.assertEqual(vocab.pack_id, "example-support")
        self.assertEqual(vocab.tools["lodge_complaint"].args["severity"].values, ("low", "high"))

    def test_an_unknown_top_level_key_is_refused(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "unknown key"):
            _vocab(SYNTHETIC_VOCABULARY + "extra: 1\n")

    def test_a_misspelt_tool_key_is_refused(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "unknown key"):
            _vocab(_swap(SYNTHETIC_VOCABULARY, "description: Reads", "descripton: Reads"))

    def test_a_tool_without_an_action_is_refused(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "missing"):
            _vocab(_swap(SYNTHETIC_VOCABULARY, "    action: cover-service___get_cover\n", ""))

    def test_a_comma_in_an_unquoted_inline_value_is_caught(self):
        # A real mistake made while writing the HESTA vocabulary: the comma splits the value into a stray key.
        broken = _swap(SYNTHETIC_VOCABULARY, "{ description: Why }", "{ description: A reason, in plain words }")
        with self.assertRaisesRegex(cedar_pack.CompileError, "unknown key"):
            _vocab(broken)

    def test_yaml_booleans_are_not_accepted_as_allowed_values(self):
        # `yes` and `no` parse as booleans in YAML; allowed values must be plain text.
        with self.assertRaisesRegex(cedar_pack.CompileError, "plain text"):
            _vocab(_swap(SYNTHETIC_VOCABULARY, "values: [low, high]", "values: [yes, no]"))


class CompileTests(unittest.TestCase):
    def test_an_unknown_tool_is_refused_and_the_known_ones_are_listed(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "unknown tool 'send_notification'.*get_member_cover"):
            _compile(_feature_with('the tool "send_notification" is never permitted'))

    def test_an_unknown_argument_is_refused_and_the_known_ones_are_listed(self):
        line = 'the tool "lodge_complaint" is permitted only when sevrity is "high"'
        with self.assertRaisesRegex(cedar_pack.CompileError, "no argument 'sevrity'.*severity"):
            _compile(_feature_with(line))

    def test_a_value_outside_the_allowed_values_is_refused(self):
        line = 'the tool "lodge_complaint" is permitted only when severity is "hgh"'
        with self.assertRaisesRegex(cedar_pack.CompileError, "cannot be 'hgh'.*low, high"):
            _compile(_feature_with(line))

    def test_a_condition_on_a_tool_with_no_arguments_is_refused(self):
        no_args = _swap(SYNTHETIC_VOCABULARY, "    args:\n      member_id: { description: The member }\n", "    args: {}\n")
        line = 'the tool "get_member_cover" is permitted only when member_id is "M1"'
        with self.assertRaisesRegex(cedar_pack.CompileError, "no argument 'member_id'.*none"):
            _compile(_feature_with(line), _vocab(no_args))

    def test_a_statement_outside_the_policy_vocabulary_is_refused(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "not in the policy vocabulary"):
            _compile(_feature_with('the tool "waive_excess" should probably be avoided'))

    def test_a_feature_with_no_policy_statements_is_refused(self):
        with self.assertRaisesRegex(cedar_pack.CompileError, "no @policy statements"):
            _compile('Feature: t\n  Rule: r\n    @must\n    Scenario: S\n      When the agent calls "waive_excess"\n')

    def test_the_same_inputs_give_the_same_policy(self):
        self.assertEqual(_compile(SYNTHETIC_FEATURE), _compile(SYNTHETIC_FEATURE))


class PackBuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pack = Path(self._tmp.name) / "pack"
        self.pack.mkdir()
        (self.pack / "support.feature").write_text(SYNTHETIC_FEATURE, encoding="utf-8")
        (self.pack / "vocabulary.yaml").write_text(SYNTHETIC_VOCABULARY, encoding="utf-8")

    def test_the_manifest_takes_its_identity_from_the_vocabulary(self):
        manifest = cedar_pack.build(self.pack)
        self.assertEqual((manifest["pack_id"], manifest["jurisdiction"]), ("example-support", "AU"))

    def test_the_compiled_policy_has_only_this_packs_words(self):
        cedar_pack.build(self.pack)
        policy = (self.pack / cedar_pack.POLICY_FILE).read_text()
        self.assertIn("cover-service___get_cover", policy)
        self.assertIn("pack: example-support", policy)
        for hesta_tool in ("create-claim___create_claim", "notification___send_notification", "policy-lookup___lookup_policy"):
            self.assertNotIn(hesta_tool, policy)

    def test_changing_the_vocabulary_drops_the_approval(self):
        cedar_pack.build(self.pack)
        cedar_pack._approve_fixture(self.pack)
        self.assertEqual(len(json.loads((self.pack / "manifest.json").read_text())["approvals"]), 1)

        with (self.pack / "vocabulary.yaml").open("a", encoding="utf-8") as f:
            f.write("# a harmless-looking edit\n")
        manifest = cedar_pack.build(self.pack)

        self.assertEqual(manifest["approvals"], [])
        with self.assertRaises(cedar_pack.PackRejected):
            cedar_pack.verify_pack(self.pack)

    def test_a_pack_needs_a_vocabulary(self):
        (self.pack / "vocabulary.yaml").unlink()
        with self.assertRaisesRegex(cedar_pack.CompileError, "vocabulary.yaml"):
            cedar_pack.build(self.pack)

    def test_a_pack_has_exactly_one_feature_file(self):
        (self.pack / "other.feature").write_text(SYNTHETIC_FEATURE, encoding="utf-8")
        with self.assertRaisesRegex(cedar_pack.CompileError, "exactly one"):
            cedar_pack.build(self.pack)


@unittest.skipIf(HANDLER_SKIP, HANDLER_SKIP)
class SecondVocabularyRunsUnchangedTests(unittest.TestCase):
    """A different client's words, the same compiler and loader, no edits to either."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        source = Path(self._tmp.name) / "source"
        source.mkdir()
        (source / "support.feature").write_text(SYNTHETIC_FEATURE, encoding="utf-8")
        (source / "vocabulary.yaml").write_text(SYNTHETIC_VOCABULARY, encoding="utf-8")
        self.pack = cedar_pack._fresh_pack(self._tmp.name, "approved", source)

    def test_every_scenario_passes_through_the_real_handler(self):
        handler = cedar_pack.load_cedar_authorization(self.pack)
        vocab = _vocab()
        scenarios = [s for s in cedar_pack.parse_feature(SYNTHETIC_FEATURE) if "must" in s.tags]
        self.assertEqual(len(scenarios), 4)  # 1 + 2 (outline rows) + 1
        for scenario in scenarios:
            with self.subTest(scenario=scenario.name):
                ok, why = cedar_pack._run_must(handler, vocab, scenario)
                self.assertTrue(ok, why)

    def test_the_fail_closed_loader_applies_to_it_as_well(self):
        policy_path = self.pack / cedar_pack.POLICY_FILE
        policy_path.write_text(policy_path.read_text() + "permit (principal, action, resource);\n")
        with self.assertRaises(cedar_pack.PackRejected):
            cedar_pack.load_cedar_authorization(self.pack)


@unittest.skipIf(HANDLER_SKIP, HANDLER_SKIP)
class CheckedInPackTests(unittest.TestCase):
    def test_the_checked_in_policy_is_what_the_compiler_produces_now(self):
        if not (HESTA_PACK / "manifest.json").exists():
            self.skipTest("the HESTA pack has not been compiled")
        with tempfile.TemporaryDirectory() as tmp:
            rebuilt = cedar_pack._fresh_pack(tmp, "rebuilt", HESTA_PACK)
            self.assertEqual((rebuilt / cedar_pack.POLICY_FILE).read_bytes(), (HESTA_PACK / cedar_pack.POLICY_FILE).read_bytes())


if __name__ == "__main__":
    unittest.main()
