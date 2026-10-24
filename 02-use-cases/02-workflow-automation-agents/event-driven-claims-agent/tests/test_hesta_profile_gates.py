"""Tests for the deployment profile and the gate rules agreed in ADR-0015 (Phase 3, pre-sale).

* The profile is strict unless it is exactly "presale"; a typo can only make the system stricter.
* Strict: a sender who has not passed DMARC is never "verified" (capped at partial). Presale accepts the address match.
* The two non-regulated intents take the light path only if the request is not (COD) an email change or login restore, and not
  (RTC) an instruction to move money.  The intent identifier sets two flags; a pattern check on the message backs them up.

All messages and member data are invented.

Run:
    python3 -m unittest tests.test_hesta_profile_gates -v

Needs PyYAML and `strands-agents`; skips with a reason without them.
"""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

SKIP_REASON = ""
try:
    from core import memory, profile  # noqa: E402
    from hesta import schemas, taxonomy, tools  # noqa: E402
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    SKIP_REASON = f"needs strands-agents ({exc})"

CONFIG = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))
PROMPTS = {name: agent["system_prompt"] for name, agent in CONFIG["agents"].items() if "system_prompt" in agent}

MEMBER, EMAIL = "60010001", "sarah.thompson@example.com"


def _env(**values):
    """Patch DEPLOYMENT_PROFILE; a value of None removes it."""
    patcher = mock.patch.dict(os.environ, {k: v for k, v in values.items() if v is not None})
    patcher.start()
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
    return patcher


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class GateTestCase(unittest.TestCase):
    def setUp(self):
        memory.forget_all()
        self.addCleanup(memory.forget_all)
        self.addCleanup(_env(DEPLOYMENT_PROFILE=None).stop)  # every test starts from the default

    def set_profile(self, value):
        self.addCleanup(_env(DEPLOYMENT_PROFILE=value).stop)


class ProfileTests(GateTestCase):
    def test_the_default_is_strict(self):
        self.assertEqual(profile.current(), "strict")
        self.assertFalse(profile.is_presale())

    def test_presale_must_be_asked_for_and_case_and_spaces_do_not_matter(self):
        for value in ("presale", "PRESALE", "  Presale "):
            with self.subTest(value=value):
                self.set_profile(value)
                self.assertEqual(profile.current(), "presale")

    def test_anything_else_means_strict(self):
        for value in ("pre-sale", "presales", "demo", "relaxed", "", "0", "true"):
            with self.subTest(value=value):
                self.set_profile(value)
                self.assertEqual(profile.current(), "strict")


def _verify(**overrides):
    kwargs = dict(member_number=MEMBER, sender_email=EMAIL, record_found=True, record_email=EMAIL,
                  record_status="active", record_policy_type="super_accumulation")
    kwargs.update(overrides)
    return tools.classify_verification(**kwargs)


class VerificationProfileTests(GateTestCase):
    def test_strict_with_an_authenticated_sender_is_verified_and_opens_memory(self):
        result = _verify(sender_authenticated=True)
        self.assertEqual((result["verification_level"], result["verification_required"]), ("verified", False))
        self.assertTrue(memory.is_verified(MEMBER, EMAIL))

    def test_strict_without_authentication_is_capped_at_partial_and_memory_stays_closed(self):
        result = _verify()
        self.assertEqual((result["verification_level"], result["verification_required"]), ("partial", True))
        self.assertIn("DMARC", result["notes"])
        self.assertFalse(memory.is_verified(MEMBER, EMAIL))

    def test_presale_accepts_the_address_match_and_says_so(self):
        self.set_profile("presale")
        result = _verify()
        self.assertEqual(result["verification_level"], "verified")
        self.assertIn("Pre-sale", result["notes"])
        self.assertTrue(memory.is_verified(MEMBER, EMAIL))

    def test_presale_does_not_rescue_a_mismatched_address_or_a_closed_account(self):
        self.set_profile("presale")
        self.assertEqual(_verify(record_email="someone.else@example.com")["verification_level"], "partial")
        self.assertEqual(_verify(record_status="closed")["verification_level"], "unverified")

    def test_a_mistyped_profile_is_strict(self):
        self.set_profile("pre-sale")
        self.assertEqual(_verify()["verification_level"], "partial")


def _route(message="", intent="change_of_details", **flags):
    args = dict(primary_intent_id=intent, primary_confidence=90, verification_required=False,
                verification_level="verified", latest_message=message)
    args.update(flags)
    return tools.route_decision(**args)


class ChangeOfDetailsRuleTests(GateTestCase):
    def test_a_clean_change_of_mobile_or_address_takes_the_light_path(self):
        for message in ("Please update my mobile number to 0400 000 000.", "My postal address has changed to 1 Example Street."):
            with self.subTest(message=message):
                self.assertFalse(_route(message)["escalate_to_human"])

    def test_the_intent_identifiers_flag_escalates(self):
        result = _route("hello", email_change_or_login_restore=True)
        self.assertTrue(result["escalate_to_human"])
        self.assertTrue(any("email on record or restore login access" in r for r in result["reasons"]))

    def test_the_pattern_check_catches_the_plain_cases_without_the_flag(self):
        for message in (
            "Please update my email address to new.me@example.com.",
            "I want to change my e-mail on the account.",
            "I can't log in to Member Online.",
            "I can’t access my account.",
            "Please reset my password.",
            "I forgot my password.",
            "I am locked out of my account.",
        ):
            with self.subTest(message=message):
                result = _route(message)
                self.assertTrue(result["escalate_to_human"])
                self.assertTrue(any("email on record or restore login access" in r for r in result["reasons"]))


class RolloverRuleTests(GateTestCase):
    def test_questions_and_status_enquiries_take_the_light_path(self):
        for message in (
            "How do I roll over my super?",
            "I transferred my Rest super to HESTA but it is not showing yet.",
            "What is the process to combine my accounts?",
        ):
            with self.subTest(message=message):
                self.assertFalse(_route(message, intent="rollover_transfer_combine")["escalate_to_human"])

    def test_the_intent_identifiers_flag_escalates(self):
        result = _route("hello", intent="rollover_transfer_combine", instructs_money_movement=True)
        self.assertTrue(result["escalate_to_human"])
        self.assertTrue(any("instructs or authorises a rollover or transfer" in r for r in result["reasons"]))

    def test_the_pattern_check_catches_instructions_without_the_flag(self):
        for message in (
            "Please roll over my Rest super to HESTA.",
            "I authorise you to transfer my balance from the other fund.",
            "I'd like you to combine my accounts.",
            "Go ahead and consolidate my super.",
        ):
            with self.subTest(message=message):
                result = _route(message, intent="rollover_transfer_combine")
                self.assertTrue(result["escalate_to_human"])
                self.assertTrue(any("instructs or authorises a rollover or transfer" in r for r in result["reasons"]))

    def test_a_bare_imperative_is_a_known_limit_that_relies_on_the_flag(self):
        # No marker word, so the backstop does not catch it. The intent identifier's flag is what must.
        self.assertFalse(_route("Transfer my super.", intent="rollover_transfer_combine")["escalate_to_human"])
        self.assertTrue(_route("Transfer my super.", intent="rollover_transfer_combine", instructs_money_movement=True)["escalate_to_human"])

    def test_the_new_rules_never_suppress_an_existing_escalation(self):
        regulated = next(i.id for i in taxonomy.INTENTS if i.regulated)
        result = _route("How do I roll over?", intent=regulated, verification_required=True, verification_level="partial")
        self.assertTrue(result["escalate_to_human"])
        self.assertGreaterEqual(len(result["reasons"]), 2)


class PlumbingTests(GateTestCase):
    def test_normalize_email_carries_the_authentication_result_and_defaults_to_false(self):
        self.assertFalse(tools.normalize_email(raw="Hello", sender_email=EMAIL)["sender_authenticated"])
        self.assertTrue(tools.normalize_email(raw="Hello", sender_email=EMAIL, sender_authenticated=True)["sender_authenticated"])

    def test_the_intent_result_has_the_two_flags_defaulting_to_false(self):
        result = schemas.IntentResult(intents=[], primary_intent_id="other_unknown", sender_type="member")
        self.assertFalse(result.email_change_or_login_restore)
        self.assertFalse(result.instructs_money_movement)
        flagged = schemas.IntentResult(intents=[], primary_intent_id="x", sender_type="member",
                                       email_change_or_login_restore=True, instructs_money_movement=True)
        self.assertTrue(flagged.email_change_or_login_restore and flagged.instructs_money_movement)


class ConfigTests(unittest.TestCase):
    def test_the_profile_defaults_to_strict_in_the_config(self):
        match = re.fullmatch(r"\$\{DEPLOYMENT_PROFILE:-(.+)\}", CONFIG["vars"]["profile"])
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "strict")

    def test_the_coordinator_stamps_a_presale_report_and_shows_the_profile(self):
        prompt = PROMPTS["coordinator"]
        self.assertIn('exactly "presale"', prompt)
        self.assertIn("PRE-SALE DEMO: synthetic data only, not for real members", prompt)
        self.assertIn("**Profile:** ${profile}", prompt)

    def test_the_stamp_text_in_the_prompt_matches_the_core(self):
        if SKIP_REASON:
            self.skipTest(SKIP_REASON)
        self.assertIn(profile.STAMP, PROMPTS["coordinator"])

    def test_the_coordinator_passes_the_new_facts_to_the_gate(self):
        prompt = PROMPTS["coordinator"]
        for name in ("email_change_or_login_restore", "instructs_money_movement", "latest_message", "sender_authenticated"):
            with self.subTest(argument=name):
                self.assertIn(name, prompt)

    def test_the_identity_agent_is_told_never_to_set_the_authentication_flag_itself(self):
        self.assertIn("Never set it to true yourself", PROMPTS["identity_profiler"])

    def test_the_intent_identifier_is_told_what_sets_each_flag(self):
        prompt = PROMPTS["intent_identifier"]
        self.assertIn("email_change_or_login_restore = true", prompt)
        self.assertIn("instructs_money_movement = true", prompt)


if __name__ == "__main__":
    unittest.main()
