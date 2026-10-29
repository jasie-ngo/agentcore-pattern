"""Tests for the agent blueprints (app/_shared/agent_blueprints.py), the local
tool builders (app/_shared/local_tools.py) and the card-dispute config built
from them.

The *_spec functions turn a YAML form into (output schema, system prompt)
without building an agent, so most tests need only pydantic. Loading the full
config needs strands-compose (run under the agent venv).

Run:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "app"))

try:
    from _shared import agent_blueprints as ab
    from pydantic import ValidationError

    _PYDANTIC_AVAILABLE = True
except ImportError:
    _PYDANTIC_AVAILABLE = False

try:
    from _shared import local_tools  # needs strands
    from strands_compose import load

    _STRANDS_AVAILABLE = True
except ImportError:
    _STRANDS_AVAILABLE = False

LABELS = [
    {"id": "card_dispute", "description": "disputes a charge", "signals": ["don't recognise"]},
    {"id": "fee_query", "description": "asks about a fee"},
]


@unittest.skipUnless(_PYDANTIC_AVAILABLE, "pydantic not installed (run under the agent venv)")
class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self.model, self.prompt, self.rest = ab.classifier_spec(
            "intent_classifier",
            labels=LABELS,
            flags={"fraud_suspected": "several unknown charges"},
            context="You work for a bank.",
            task="Classify the email.",
            tools=[],
        )

    def test_prompt_lists_labels_signals_and_flags(self):
        for text in (
            "You work for a bank.",
            "Classify the email.",
            "card_dispute",
            "don't recognise",
            "fraud_suspected",
            '"other"',
        ):
            self.assertIn(text, self.prompt)

    def test_schema_accepts_known_labels_and_adds_fallback(self):
        result = self.model(
            labels=[{"id": "other", "confidence": 40, "rationale": "r"}],
            primary_id="other",
            needs_triage=True,
            fraud_suspected=False,
        )
        self.assertEqual(result.primary_id, "other")

    def test_schema_rejects_unknown_labels(self):
        with self.assertRaises(ValidationError):
            self.model(labels=[], primary_id="mortgage", needs_triage=False, fraud_suspected=False)

    def test_confidence_bounded(self):
        with self.assertRaises(ValidationError):
            self.model(
                labels=[{"id": "fee_query", "confidence": 150, "rationale": "r"}],
                primary_id="fee_query",
                needs_triage=False,
                fraud_suspected=False,
            )

    def test_unused_kwargs_pass_through_to_agent(self):
        self.assertEqual(self.rest, {"tools": []})

    def test_fields_are_extra_optional_outputs(self):
        model, prompt, _ = ab.classifier_spec(
            "c", labels=LABELS, fields={"sender_type": {"enum": ["member", "unknown"], "description": "who writes"}}
        )
        self.assertIn("sender_type: who writes (one of: member, unknown)", prompt)
        self.assertIsNone(model(labels=[], primary_id="other", needs_triage=True).sender_type)
        with self.assertRaises(ValidationError):
            model(labels=[], primary_id="other", needs_triage=True, sender_type="robot")

    def test_labels_required(self):
        with self.assertRaisesRegex(ValueError, "labels is required"):
            ab.classifier_spec("x", labels=[])


@unittest.skipUnless(_PYDANTIC_AVAILABLE, "pydantic not installed (run under the agent venv)")
class OtherBlueprintTests(unittest.TestCase):
    def test_extractor_fields_are_optional_and_typed(self):
        model, prompt, _ = ab.extractor_spec(
            "x", fields={"amount": {"type": "number"}, "kind": {"enum": ["a", "b"], "description": "Kind"}}
        )
        self.assertIsNone(model().amount)
        self.assertEqual(model(amount="12.5").amount, 12.5)
        with self.assertRaises(ValidationError):
            model(kind="c")
        self.assertIn("(one of: a, b)", prompt)

    def test_extractor_bad_type(self):
        with self.assertRaisesRegex(ValueError, "type must be one of"):
            ab.extractor_spec("x", fields={"a": {"type": "date"}})

    def test_assessor_outcomes_facts_and_fallback(self):
        model, prompt, _ = ab.assessor_spec(
            "x",
            outcomes=[{"id": "ok", "when": "all good"}, "unclear"],
            facts={"customer_id": {"type": "string"}},
            criteria=["Look it up."],
            fallback_outcome="unclear",
        )
        result = model(outcome="ok", confidence=90, reasons=["r"])
        self.assertIsNone(result.customer_id)
        self.assertIn('choose "unclear"', prompt)
        self.assertIn("ok: all good", prompt)
        with self.assertRaisesRegex(ValueError, "fallback_outcome"):
            ab.assessor_spec("x", outcomes=["ok"], fallback_outcome="nope")

    def test_reviewer_checks_become_fields(self):
        model, prompt, _ = ab.reviewer_spec("x", checks={"facts_ok": "facts are right"})
        self.assertEqual(model(facts_ok=True, approved=True, confidence=80).issues, [])
        self.assertIn("approved is true only if EVERY check is true", prompt)
        with self.assertRaisesRegex(ValueError, "snake_case"):
            ab.reviewer_spec("x", checks={"Facts OK": "x"})

    def test_writer_prompt_has_style_knowledge_and_signoff(self):
        model, prompt, _ = ab.writer_spec(
            "x", style=["Warm."], knowledge={"refund": "We refund."}, signoff="Regards", system_prompt="Be brief."
        )
        self.assertIs(model, ab.Draft)
        for text in ("Warm.", "[refund]", "We refund.", "Regards", "Be brief."):
            self.assertIn(text, prompt)


@unittest.skipUnless(_STRANDS_AVAILABLE, "strands-compose not installed (run under the agent venv)")
class LocalToolTests(unittest.TestCase):
    def test_decision_table_tool_spec_and_run(self):
        tool = local_tools.decision_table_tool(os.path.join(_ROOT, "app", "card_dispute_agent", "decisions.yaml"))
        self.assertEqual(tool.tool_name, "decide_dispute")
        self.assertIn("amount", tool.tool_spec["inputSchema"]["json"]["properties"])
        result = tool._tool_func({"toolUseId": "t1", "input": {"intent": "fee_query"}})
        self.assertEqual(result["content"][0]["json"]["outcome"], "route_to_service_team")

    def test_ingestion_tool_run(self):
        tool = local_tools.ingestion_tool(
            os.path.join(_ROOT, "app", "card_dispute_agent", "ingestion.yaml"), use_case="card_dispute_agent"
        )
        env = tool._tool_func({"toolUseId": "t", "input": {"raw": "Disputed charge, CUST-100001"}})["content"][0][
            "json"
        ]
        self.assertEqual((env["id_for_lookup"], env["use_case"]), ("CUST-100001", "card_dispute_agent"))
        self.assertNotIn("raw_text", env)


@unittest.skipUnless(_STRANDS_AVAILABLE, "strands-compose not installed (run under the agent venv)")
class CardDisputeConfigTests(unittest.TestCase):
    """The use case is built from blueprints only — check what they produced."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("AWS_REGION", "us-west-2")
        cls.agents = load(os.path.join(_ROOT, "app", "card_dispute_agent", "config.yaml"), session_id="t" * 40).agents

    def test_every_specialist_has_a_generated_schema(self):
        expected = {
            "intent_classifier": "IntentClassifierResult",
            "dispute_extractor": "DisputeExtractorResult",
            "dispute_assessor": "DisputeAssessorResult",
            "dispute_reviewer": "DisputeReviewerResult",
            "reply_writer": "Draft",
        }
        actual = {n: getattr(self.agents[n]._default_structured_output_model, "__name__", None) for n in expected}
        self.assertEqual(actual, expected)

    def test_yaml_variables_reach_blueprint_prompts(self):
        prompt = self.agents["reply_writer"].system_prompt
        self.assertIn("Harbour Bank", prompt)
        self.assertNotIn("${", prompt)

    def test_coordinator_tools(self):
        self.assertEqual(sorted(self.agents["coordinator"].tool_names), ["decide_dispute", "normalize_email"])


@unittest.skipUnless(_STRANDS_AVAILABLE, "strands-compose not installed (run under the agent venv)")
class HestaConfigTests(unittest.TestCase):
    """HESTA: every specialist is a blueprint; the deterministic steps are decision tables."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("AWS_REGION", "us-west-2")
        cls.agents = load(os.path.join(_ROOT, "app", "hesta_agent", "config.yaml"), session_id="t" * 40).agents

    def test_every_specialist_has_a_generated_schema(self):
        expected = {
            "intent_identifier": "IntentIdentifierResult",
            "context_manager": "ContextManagerResult",
            "empathy": "EmpathyResult",
            "writer": "Draft",
            "reviewer_editor": "ReviewerEditorResult",
        }
        actual = {n: getattr(self.agents[n]._default_structured_output_model, "__name__", None) for n in expected}
        self.assertEqual(actual, expected)

    def test_classifier_keeps_taxonomy_flag_and_sender_type(self):
        model = self.agents["intent_identifier"]._default_structured_output_model
        self.assertIn("personal_advice_requested", model.model_fields)
        self.assertIn("sender_type", model.model_fields)
        prompt = self.agents["intent_identifier"].system_prompt
        self.assertIn("departing_australia_payment", prompt)
        self.assertEqual(prompt.count("- id: other_unknown"), 1)

    def test_writer_has_the_approved_knowledge(self):
        prompt = self.agents["writer"].system_prompt
        for text in ("[personal_advice_decline]", "[identity_verification]", "AFSL 235249", "Date of birth"):
            self.assertIn(text, prompt)

    def test_yaml_variables_reach_prompts(self):
        self.assertIn("[GUARDRAIL_BLOCKED_ADVICE]", self.agents["reviewer_editor"].system_prompt)
        self.assertNotIn("${", self.agents["coordinator"].system_prompt)

    def test_coordinator_tools(self):
        self.assertEqual(
            sorted(self.agents["coordinator"].tool_names),
            ["check_attachments", "normalize_email", "route_case", "verify_identity"],
        )


@unittest.skipUnless(_STRANDS_AVAILABLE, "strands-compose not installed (run under the agent venv)")
class ClaimsConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("AWS_REGION", "us-west-2")
        cls.agents = load(os.path.join(_ROOT, "app", "claims_agent", "config.yaml"), session_id="t" * 40).agents

    def test_specialists_are_blueprints(self):
        processor = self.agents["claims_processor"]._default_structured_output_model
        validator = self.agents["claims_validator"]._default_structured_output_model
        self.assertTrue({"outcome", "confidence", "reasons", "amount", "policy_number"} <= set(processor.model_fields))
        self.assertTrue({"approved", "confidence", "issues", "decision_supported"} <= set(validator.model_fields))

    def test_processor_falls_back_to_reject(self):
        self.assertIn('choose "REJECT"', self.agents["claims_processor"].system_prompt)

    def test_coordinator_tools(self):
        self.assertEqual(self.agents["coordinator"].tool_names, ["execute_claim_decision"])


if __name__ == "__main__":
    unittest.main()
