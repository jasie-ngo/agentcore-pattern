"""The illustrative finance-insurer client: its package, its overlay and its guardrail wording.

What is proved:
  * The package is self-contained: nothing in it, its overlay or its guardrail file names another client or imports its package.
  * The identity rule does not lock out the people the hardship path is for: a policy in arrears or lapsed still verifies.
  * The routing gate escalates every decision only a person may make, every hardship signal (by flag OR by message text), every
    third-party sender and every cancellation or login-takeover request, and lets only routine information requests through.
  * The overlay agrees with the code: the taxonomy block, the tool names and the structured-output classes all exist.
  * Assembling the client ships its package and not the first client's, and the real loader builds every agent from the result.

All messages, policy numbers and addresses are invented. The wording is illustrative, not a real insurer's.

Run:
    python3 -m unittest tests.test_fininsurer -v

Needs PyYAML and `strands-agents`; skips with a reason without them.
"""

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
PKG = APP / "fininsurer"
OVERLAY = ROOT / "clients" / "fininsurer" / "pipeline" / "pipeline.yaml"
GUARDRAIL = ROOT / "clients" / "fininsurer" / "guardrail" / "topics.json"
OTHER_CLIENTS = ("hesta", "medibank")
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

SKIP_REASON = ""
try:
    import yaml  # noqa: E402
    from core import memory  # noqa: E402
    from fininsurer import knowledge, schemas, taxonomy, tools  # noqa: E402
    from fininsurer.ingestion import normalize_email  # noqa: E402
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    SKIP_REASON = f"needs PyYAML and strands-agents ({exc})"

_spec = importlib.util.spec_from_file_location("assemble_client", ROOT / "scripts" / "assemble_client.py")
assemble_client = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("assemble_client", assemble_client)
_spec.loader.exec_module(assemble_client)

POLICY, EMAIL = "POL-12345", "alex.chen@example.com"
ALLOWED_IMPORTS = {"core", "fininsurer", "strands", "pydantic"}


def _imports(path: Path) -> set:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class GateTestCase(unittest.TestCase):
    def setUp(self):
        memory.forget_all()
        self.addCleanup(memory.forget_all)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("DEPLOYMENT_PROFILE", None)  # every test starts strict

    def presale(self):
        os.environ["DEPLOYMENT_PROFILE"] = "presale"


def _verify(**overrides):
    args = dict(policy_number=POLICY, sender_email=EMAIL, record_found=True, record_email=EMAIL, record_status="active",
                record_policy_type="home", sender_authenticated=True)
    args.update(overrides)
    return tools.classify_verification(**args)


def _route(message="", intent="claim_status", **flags):
    args = dict(primary_intent_id=intent, primary_confidence=90, verification_required=False, verification_level="verified",
                latest_message=message)
    args.update(flags)
    return tools.route_decision(**args)


# ─── Self-containment ────────────────────────────────────────────────────────


class SelfContainedTests(unittest.TestCase):
    def test_nothing_in_the_package_overlay_or_guardrail_names_another_client(self):
        files = [*sorted(PKG.glob("*.py")), OVERLAY, GUARDRAIL]
        self.assertGreaterEqual(len(files), 9)
        for path in files:
            text = path.read_text(encoding="utf-8").lower()
            for name in OTHER_CLIENTS:
                with self.subTest(file=path.name, client=name):
                    self.assertNotIn(name, text)

    def test_the_package_imports_only_the_core_itself_and_allowed_libraries(self):
        for path in sorted(PKG.glob("*.py")):
            unexpected = {m for m in _imports(path) if m not in sys.stdlib_module_names and m not in ALLOWED_IMPORTS}
            with self.subTest(file=path.name):
                self.assertEqual(unexpected, set())

    def test_the_wording_is_labelled_illustrative(self):
        for path in (PKG / "knowledge.py", PKG / "taxonomy.py", PKG / "__init__.py", OVERLAY):
            with self.subTest(file=path.name):
                self.assertIn("ILLUSTRATIVE", path.read_text(encoding="utf-8").upper())


# ─── Taxonomy and overlay agree with the code ────────────────────────────────


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class OverlayAgreesWithCodeTests(unittest.TestCase):
    def setUp(self):
        self.overlay = yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))

    def test_the_intent_ids_are_unique_and_the_hardship_intents_need_a_person(self):
        ids = [i.id for i in taxonomy.INTENTS]
        self.assertEqual(len(ids), len(set(ids)))
        for intent_id in taxonomy.HARDSHIP_IDS:
            self.assertIn(intent_id, ids)
            self.assertTrue(taxonomy.is_human_decision(intent_id))
        for routine in ("claim_status", "change_of_details", "policy_enquiry"):
            self.assertFalse(taxonomy.is_human_decision(routine))

    def test_the_overlays_taxonomy_block_is_exactly_what_the_code_renders(self):
        # Run taxonomy.render_for_prompt() after editing INTENTS and paste it back into the overlay.
        self.assertIn(taxonomy.render_for_prompt(), self.overlay["agents"]["intent_identifier"]["system_prompt"])

    def test_every_tool_the_overlay_names_exists(self):
        for name, agent in self.overlay["agents"].items():
            for ref in [agent.get("type"), *(agent.get("tools") or [])]:
                module, _, function = ref.partition(":")
                with self.subTest(agent=name, ref=ref):
                    self.assertTrue(module.startswith("./fininsurer/"))
                    if Path(module).name == "tools.py":
                        self.assertTrue(hasattr(tools, function), f"{function} is not in {module}")
                    else:
                        self.assertEqual((Path(module).name, function), ("factory.py", "fininsurer_agent"))

    def test_every_structured_output_class_exists(self):
        for name, agent in self.overlay["agents"].items():
            cls = (agent.get("agent_kwargs") or {}).get("structured_output")
            if cls:
                with self.subTest(agent=name):
                    self.assertTrue(hasattr(schemas, cls), f"{cls} is not in schemas.py")

    def test_the_flags_the_coordinator_passes_to_the_gate_come_from_real_schema_fields(self):
        prompt = self.overlay["agents"]["coordinator"]["system_prompt"]
        for flag in ("hardship_indicated", "personal_advice_requested", "instructs_cancellation", "contact_detail_change"):
            with self.subTest(flag=flag):
                self.assertIn(flag, prompt)
                self.assertIn(flag, schemas.IntentResult.model_fields)
        self.assertIn("complaint_indicator", prompt)
        self.assertIn("complaint_indicator", schemas.EmpathyAssessment.model_fields)
        self.assertIn("sender_type", prompt)

    def test_the_guardrail_sentinel_matches_the_one_the_reviewer_looks_for(self):
        from fininsurer.settings import GUARDRAIL_BLOCK_SENTINEL  # noqa: PLC0415

        self.assertIn(GUARDRAIL_BLOCK_SENTINEL, self.overlay["agents"]["reviewer_editor"]["system_prompt"])


# ─── Identity ────────────────────────────────────────────────────────────────


class IdentityTests(GateTestCase):
    def test_a_policy_in_arrears_or_lapsed_still_verifies(self):
        # The point of the rule: a hardship customer is often behind on premiums and must not fail verification for it.
        for status in ("active", "in_arrears", "lapsed", "LAPSED"):
            with self.subTest(status=status):
                self.assertEqual(_verify(record_status=status)["verification_level"], "verified")

    def test_a_cancelled_or_unknown_status_is_capped_at_partial_and_goes_to_a_person(self):
        for status in ("cancelled", "closed", ""):
            with self.subTest(status=status):
                result = _verify(record_status=status)
                self.assertEqual(result["verification_level"], "partial")
                self.assertTrue(result["verification_required"])

    def test_strict_caps_an_unauthenticated_sender_at_partial_and_presale_accepts_the_address(self):
        self.assertEqual(_verify(sender_authenticated=False)["verification_level"], "partial")
        self.presale()
        result = _verify(sender_authenticated=False)
        self.assertEqual(result["verification_level"], "verified")
        self.assertIn("Pre-sale", result["notes"])

    def test_a_mismatched_address_no_record_and_no_number_are_never_verified(self):
        self.assertEqual(_verify(record_email="someone.else@example.com")["verification_level"], "partial")
        self.assertEqual(_verify(record_found=False)["verification_level"], "unverified")
        self.assertEqual(_verify(policy_number="")["verification_level"], "unverified")

    def test_a_lookup_that_never_ran_is_told_apart_from_one_that_found_nothing(self):
        self.assertEqual(_verify(record_found=False, gateway_unavailable=True)["match_key"], None)
        self.assertEqual(_verify(record_found=False)["match_key"], "none")

    def test_only_a_verified_policyholder_opens_memory(self):
        _verify(sender_authenticated=False)  # partial under strict
        self.assertFalse(memory.is_verified(POLICY, EMAIL))
        _verify(record_status="in_arrears")  # verified
        self.assertTrue(memory.is_verified(POLICY, EMAIL))

    def test_the_memory_tools_refuse_an_unverified_sender(self):
        self.assertFalse(tools.recall_customer_memory(policy_number=POLICY, sender_email=EMAIL, latest_message="hi")["allowed"])
        self.assertFalse(tools.remember_customer_contact(policy_number=POLICY, sender_email=EMAIL, latest_message="hi", summary="s")["recorded"])


# ─── The routing gate ────────────────────────────────────────────────────────


class RoutingTests(GateTestCase):
    def test_a_routine_information_request_from_a_verified_policyholder_takes_the_light_path(self):
        for message, intent in (("Where is my claim up to?", "claim_status"), ("Please update my postal address.", "change_of_details"),
                                ("When does my policy renew?", "policy_enquiry")):
            with self.subTest(message=message):
                result = _route(message, intent)
                self.assertFalse(result["escalate_to_human"], result["reasons"])
                self.assertEqual(result["priority_floor"], "normal")

    def test_every_intent_only_a_person_may_decide_escalates(self):
        for intent in sorted(taxonomy.HUMAN_DECISION_IDS):
            with self.subTest(intent=intent):
                result = _route("Hello.", intent)
                self.assertTrue(result["escalate_to_human"])
                self.assertTrue(result["human_decision_intent"])

    def test_hardship_in_the_message_escalates_even_when_the_intent_looks_routine(self):
        for message in (
            "I lost my job and I can't pay my premiums.",
            "I'm struggling to pay the bills.",
            "We are behind on our payments.",
            "I was made redundant last week. Where is my claim?",
            "I can’t afford the excess.",
            "Is there a payment plan?",
            "I'd like to apply for financial hardship assistance.",
        ):
            with self.subTest(message=message):
                result = _route(message)  # claim_status: routine on its own
                self.assertTrue(result["escalate_to_human"])
                self.assertTrue(result["hardship"])
                self.assertEqual(result["priority_floor"], "high")

    def test_hardship_is_escalated_from_the_flag_alone_when_the_wording_gives_nothing_away(self):
        result = _route("Things are tight at the moment.", hardship_indicated=True)
        self.assertTrue(result["escalate_to_human"] and result["hardship"])

    def test_ordinary_messages_do_not_look_like_hardship(self):
        for message in ("When does my policy renew?", "I've moved house, please update my address.", "How does cancelling work?"):
            with self.subTest(message=message):
                self.assertFalse(_route(message, "policy_enquiry")["hardship"])

    def test_a_cancellation_instruction_escalates_but_a_question_about_it_does_not(self):
        for message in ("Please cancel my policy.", "I want to cancel my insurance.", "Cancel my direct debit."):
            with self.subTest(message=message):
                self.assertIn("request to cancel, reduce or pause cover", _route(message, "policy_enquiry")["reasons"])
        self.assertFalse(_route("How does cancelling work?", "policy_enquiry")["escalate_to_human"])
        self.assertTrue(_route("hello", "policy_enquiry", instructs_cancellation=True)["escalate_to_human"])

    def test_an_email_change_or_login_restore_is_the_account_takeover_route(self):
        for message in ("Please change the email on my account.", "I can't log in.", "Reset my password please."):
            with self.subTest(message=message):
                self.assertTrue(_route(message, "change_of_details")["escalate_to_human"])
        self.assertTrue(_route("hello", "change_of_details", contact_detail_change=True)["escalate_to_human"])

    def test_a_third_party_sender_always_escalates(self):
        result = _route("Where is my client's claim?", sender_type="third_party")
        self.assertTrue(result["escalate_to_human"])
        self.assertTrue(any("third party" in r for r in result["reasons"]))

    def test_each_remaining_reason_escalates_on_its_own(self):
        cases = {
            "unverified": dict(verification_required=True, verification_level="partial"),
            "unknown intent": dict(intent="other_unknown"),
            "triage flag": dict(needs_human_triage=True),
            "low confidence": dict(primary_confidence=40),
            "multiple intents": dict(confident_intent_count=2),
            "personal advice": dict(personal_advice_requested=True),
            "vulnerability": dict(vulnerability_flags=["bereavement"]),
            "high priority": dict(priority="urgent"),
            "complaint": dict(complaint_indicator=True),
        }
        for label, flags in cases.items():
            with self.subTest(reason=label):
                flags = dict(flags)
                intent = flags.pop("intent", "claim_status")
                self.assertTrue(_route("Hello.", intent, **flags)["escalate_to_human"])

    def test_a_flag_for_safety_or_distress_raises_the_priority_floor(self):
        self.assertEqual(_route("Hello.", vulnerability_flags=["family_violence"])["priority_floor"], "high")
        self.assertEqual(_route("Hello.", vulnerability_flags=["language_support"])["priority_floor"], "normal")

    def test_a_low_confidence_threshold_is_honoured_at_the_boundary(self):
        from fininsurer.settings import INTENT_CONFIDENCE_THRESHOLD as threshold  # noqa: PLC0415

        self.assertFalse(_route("Hello.", primary_confidence=threshold)["escalate_to_human"])
        self.assertTrue(_route("Hello.", primary_confidence=threshold - 1)["escalate_to_human"])


# ─── Ingestion, attachments, case status, wording ────────────────────────────


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class IngestionTests(unittest.TestCase):
    def test_a_direct_email_loses_its_banner_footer_and_quoted_history(self):
        raw = (
            "WARNING: This email originated from outside the organisation.\n"
            "Hi, my policy number is POL-12345. I lost my job.\n[ATTACHMENT payslip.pdf]\n"
            "On Monday, Support wrote:\n> earlier message\n"
            "Illustrative Insurer Pty Ltd (fictitious)\n"
        )
        email = normalize_email(raw, sender_email=EMAIL)
        self.assertEqual(email.policy_number_for_lookup, "POL-12345")
        self.assertEqual(email.attachment_count, 1)
        self.assertEqual(email.from_email, EMAIL)
        self.assertNotIn("earlier message", email.latest_message)
        self.assertNotIn("WARNING", email.latest_message)
        self.assertEqual((email.channel, email.sender_type), ("direct_email", "policyholder"))

    def test_a_web_form_is_parsed_by_its_labels(self):
        raw = ("Email address: alex.chen@example.com\nPolicy number: POL-777\nReason for enquiry: Claim\n"
               "Message: Any update on my claim?\nThanks")
        email = normalize_email(raw)
        self.assertEqual((email.channel, email.policy_number_for_lookup, email.form_reason), ("web_form", "POL-777", "Claim"))
        self.assertEqual(email.from_email, "alex.chen@example.com")
        self.assertIn("Any update", email.latest_message)

    def test_someone_writing_on_a_policyholders_behalf_is_a_third_party(self):
        email = normalize_email("I am a financial counsellor writing on behalf of my client, policy POL-9.", sender_email="fc@example.org")
        self.assertEqual((email.channel, email.sender_type), ("third_party", "third_party"))

    def test_a_placeholder_policy_number_is_treated_as_absent(self):
        email = normalize_email("Policy number: [POLICY NUMBER]\nMessage: hello", sender_email=EMAIL)
        self.assertIsNone(email.policy_number_for_lookup)

    def test_the_trigger_prefix_is_removed_and_authentication_is_carried_through(self):
        email = normalize_email("process this email: Hello, POL-5 here.", sender_email=EMAIL, sender_authenticated=True)
        self.assertNotIn("process this", email.latest_message.lower())
        self.assertTrue(email.sender_authenticated)

    def test_the_tool_returns_the_fields_the_overlay_tells_the_coordinator_to_use(self):
        fields = tools.normalize_email(raw="Hello POL-1", sender_email=EMAIL)
        for name in ("channel", "sender_type", "from_email", "policy_number_for_lookup", "latest_message", "attachment_count",
                     "form_reason", "sender_authenticated"):
            with self.subTest(field=name):
                self.assertIn(name, fields)
        self.assertNotIn("raw", fields)


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class AttachmentAndCaseTests(unittest.TestCase):
    def test_missing_hardship_evidence_never_becomes_a_condition_of_help(self):
        result = tools.assess_attachments(primary_intent_id="hardship_premium_relief", attachment_count=0)
        self.assertEqual(result["status"], "missing")
        self.assertIn("invite them", result["notes"])

    def test_evidence_present_is_unverified_until_a_person_reads_it(self):
        result = tools.assess_attachments(primary_intent_id="hardship_claim_support", attachment_count=2)
        self.assertEqual(result["status"], "present_unverified")
        self.assertIn("a person must review", result["notes"])

    def test_no_attachment_expected_and_none_sent_is_not_applicable(self):
        self.assertEqual(tools.assess_attachments(primary_intent_id="claim_status", attachment_count=0)["status"], "not_applicable")

    def test_only_this_policyholders_pending_cases_are_kept(self):
        claims = [{"policy_number": POLICY, "claim_id": "CLM-1"}, {"policy_number": "POL-OTHER", "claim_id": "CLM-2"}, "junk"]
        result = tools.filter_customer_cases(claims=claims, policy_number=POLICY)
        self.assertEqual([c["claim_id"] for c in result["customer_pending"]], ["CLM-1"])
        self.assertEqual(result["total_pending"], 2)
        self.assertFalse(tools.filter_customer_cases(claims=claims, policy_number="")["checked"])


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class KnowledgeTests(unittest.TestCase):
    def test_every_intent_has_its_own_guidance(self):
        fallback = knowledge.snippet_for("other_unknown")
        for intent in taxonomy.INTENTS:
            with self.subTest(intent=intent.id):
                self.assertNotEqual(knowledge.snippet_for(intent.id), fallback)

    def test_no_snippet_promises_an_outcome(self):
        for intent_id in taxonomy.VALID_IDS:
            text = knowledge.snippet_for(intent_id).lower()
            with self.subTest(intent=intent_id):
                for promise in ("will be approved", "will be waived", "will receive", "we will pay", "guarantee"):
                    self.assertNotIn(promise, text)

    def test_the_hardship_guidance_forbids_hinting_at_relief(self):
        for intent_id in sorted(taxonomy.HARDSHIP_IDS):
            self.assertIn("Do NOT confirm or hint at", knowledge.snippet_for(intent_id))

    def test_the_personal_advice_decline_gives_no_advice(self):
        # The decline names the advice it will not give ("whether you should keep, change or cancel"), so "whether you should" is
        # fine. What it must never do is recommend: any other "you should", or a recommendation or suggestion of ours.
        text = knowledge.PERSONAL_ADVICE_DECLINE.lower()
        self.assertEqual(re.findall(r"(?<!whether )you should", text), [])
        for advice in ("we recommend", "i recommend", "we suggest", "our advice is", "you ought to"):
            self.assertNotIn(advice, text)
        self.assertIn("not able to give you personal advice", text)

    def test_the_writers_tool_returns_everything_the_prompt_asks_it_to_use(self):
        result = tools.fininsurer_knowledge(intent_id="hardship_premium_relief")
        for key in ("snippet", "style_guide", "greeting", "signoff", "legal_footer", "personal_advice_decline"):
            self.assertIn(key, result)


# ─── The assembled client ────────────────────────────────────────────────────

# main.py's docstring, the app folder's own name and these READMEs still name the first client. Recorded in AGENTS.md as not done.
KNOWN_TO_NAME_FIRST_CLIENT = {"main.py", "README.md", "pyproject.toml", "uv.lock"}


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class AssembledClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.info = assemble_client.assemble("fininsurer", Path(cls._tmp.name) / "out", display_name="Illustrative Insurer",
                                            profile="presale", pack_dir=Path(cls._tmp.name) / "no-pack")
        cls.out = Path(cls.info["out"])
        cls.app = cls.out / "app" / "04_hesta_member_email"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_image_holds_this_clients_package_and_not_the_first_clients(self):
        self.assertTrue((self.app / "fininsurer" / "factory.py").is_file())
        self.assertTrue((self.app / "core" / "pipeline.base.yaml").is_file())
        self.assertFalse((self.app / "hesta").exists())
        self.assertFalse((self.app / "sample_emails").exists())
        self.assertTrue(self.info["pipeline_overlay"] and self.info["guardrail_included"])

    def test_nothing_that_ships_names_the_first_client_apart_from_the_known_leftovers(self):
        checked = 0
        for path in sorted(self.app.rglob("*")):
            if not path.is_file() or path.name in KNOWN_TO_NAME_FIRST_CLIENT or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".yaml", ".json", ".md", ".toml", ".txt"}:
                continue
            checked += 1
            with self.subTest(file=path.relative_to(self.app).as_posix()):
                self.assertNotIn("hesta", path.read_text(encoding="utf-8", errors="ignore").lower())
        self.assertGreater(checked, 15)

    def test_the_guardrail_file_was_copied_where_the_cdk_app_looks(self):
        self.assertEqual((self.out / "agentcore" / "guardrail.json").read_bytes(), GUARDRAIL.read_bytes())

    def test_the_merged_config_passes_the_real_schema_and_the_real_loader_builds_every_agent(self):
        try:
            from strands_compose import load, load_config
        except ImportError as exc:
            self.skipTest(f"needs strands-compose ({exc})")
        config_path = self.app / "config.yaml"
        self.assertEqual(len(load_config(str(config_path)).agents), 10)
        with mock.patch.dict(os.environ, {"AWS_REGION": "ap-southeast-2", "AWS_DEFAULT_REGION": "ap-southeast-2"}):
            resolved = load(str(config_path), session_id="fininsurer-test-0000000000000000000000000")
        self.assertEqual(len(resolved.agents), 10)
        coordinator_tools = set(resolved.agents["coordinator"].tool_names)
        self.assertTrue({"normalize_email", "route_decision", "recall_customer_memory", "remember_customer_contact"} <= coordinator_tools,
                        coordinator_tools)


@unittest.skipUnless(shutil.which("node") and (ROOT / "agentcore" / "cdk" / "dist" / "lib" / "guardrail-topics.js").is_file(),
                     "needs Node and a built CDK app (npm test in agentcore/cdk builds it)")
class GuardrailFileTests(unittest.TestCase):
    def test_the_real_validator_accepts_the_clients_guardrail_file(self):
        # One validator, in TypeScript, at synth. This runs it on the file so a wrong limit is caught here, not at deploy.
        script = "const g = require('./dist/lib/guardrail-topics'); const c = g.loadGuardrailConfig(process.argv[1]); console.log(JSON.stringify(c.topics.map(t => t.name)))"
        result = subprocess.run(["node", "-e", script, str(GUARDRAIL)], cwd=ROOT / "agentcore" / "cdk", capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertEqual(json.loads(result.stdout), ["PersonalAdvice", "OutcomeCommitments"])

    def test_the_file_has_no_sentinel_because_that_is_not_configurable(self):
        self.assertNotIn("GUARDRAIL_BLOCKED", GUARDRAIL.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
