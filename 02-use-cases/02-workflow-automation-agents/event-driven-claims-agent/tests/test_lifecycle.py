"""core/lifecycle.py: the default lifecycle, client extension, and the rules a client cannot weaken.

Run:  ./.venv/bin/python3 -m unittest tests.test_lifecycle -v
"""

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core import lifecycle as lc  # noqa: E402

BASE = lc.default()


class DefaultTests(unittest.TestCase):
    def test_every_state_takes_an_inbound_email_and_lands_in_awaiting_staff(self):
        for state in BASE.states:
            if state in BASE.terminal:
                continue
            self.assertEqual(lc.next_state(BASE, state, "inbound_processed"), "awaiting_staff", state)

    def test_member_silence_cannot_move_a_claim_out_of_awaiting_staff(self):
        with self.assertRaises(lc.LifecycleError):
            lc.next_state(BASE, "awaiting_staff", "member_silent")

    def test_an_inbound_email_in_awaiting_staff_stays_there(self):
        self.assertEqual(lc.next_state(BASE, "awaiting_staff", "inbound_processed"), "awaiting_staff")

    def test_staff_release_follows_what_the_draft_expects(self):
        self.assertEqual(lc.next_state(BASE, "awaiting_staff", "staff_released", {"expects": "member"}), "awaiting_member")
        self.assertEqual(lc.next_state(BASE, "awaiting_staff", "staff_released", {"expects": "attachment"}), "awaiting_attachment")
        self.assertEqual(lc.next_state(BASE, "awaiting_staff", "staff_released", {"expects": "nothing"}), "resolved")

    def test_release_without_a_known_expectation_is_an_error(self):
        with self.assertRaises(lc.LifecycleError):
            lc.next_state(BASE, "awaiting_staff", "staff_released", {"expects": "bogus"})
        with self.assertRaises(lc.LifecycleError):
            lc.next_state(BASE, "awaiting_staff", "staff_released", {})

    def test_silence_returns_a_waiting_claim_to_a_person(self):
        self.assertEqual(lc.next_state(BASE, "awaiting_member", "member_silent"), "awaiting_staff")

    def test_closed_is_final(self):
        for event in lc.EVENTS:
            with self.assertRaises(lc.LifecycleError):
                lc.next_state(BASE, "closed", event)

    def test_the_default_uses_only_known_events_and_names_no_send(self):
        for t in BASE.transitions:
            self.assertIn(t.event, lc.EVENTS)
        for name in list(lc.EVENTS) + list(BASE.states):
            for word in ("send", "email", "mail", "dispatch", "smtp"):
                self.assertNotIn(word, name, name)

    def test_the_default_itself_validates_and_closes_from_resolved(self):
        out = lc.extend(BASE, {})
        self.assertEqual(lc.next_state(out, "resolved", "staff_closed"), "closed")


class ExtendTests(unittest.TestCase):
    GOOD = {
        "states": ["awaiting_specialist"],
        "expects": ["specialist"],
        "transitions": [
            {"from": "awaiting_staff", "event": "staff_released", "when": {"expects": "specialist"}, "to": "awaiting_specialist"},
            {"from": "awaiting_specialist", "event": "inbound_processed", "to": "awaiting_staff"},
            {"from": "awaiting_specialist", "event": "staff_resolved", "to": "resolved"},
        ],
        "timeouts_days": {"awaiting_member": 3},
    }

    def test_a_valid_extension_adds_states_and_overrides_a_timeout(self):
        out = lc.extend(BASE, self.GOOD)
        self.assertIn("awaiting_specialist", out.states)
        self.assertEqual(out.timeouts_days["awaiting_member"], 3)
        self.assertEqual(lc.next_state(out, "awaiting_staff", "staff_released", {"expects": "specialist"}), "awaiting_specialist")
        self.assertEqual(lc.next_state(out, "awaiting_specialist", "inbound_processed"), "awaiting_staff")

    def test_the_default_is_not_mutated(self):
        lc.extend(BASE, self.GOOD)
        self.assertNotIn("awaiting_specialist", BASE.states)

    def refuse(self, overlay, pattern):
        with self.assertRaisesRegex(lc.LifecycleError, pattern):
            lc.extend(BASE, overlay)

    def test_cannot_redefine_a_default_transition(self):
        self.refuse({"transitions": [{"from": "awaiting_member", "event": "inbound_processed", "to": "resolved"}]}, "default transition")

    def test_cannot_add_an_exit_from_awaiting_staff_on_a_non_staff_event(self):
        self.refuse({"states": ["x"], "transitions": [
            {"from": "awaiting_staff", "event": "member_silent", "to": "x"},
            {"from": "x", "event": "inbound_processed", "to": "awaiting_staff"}]}, "awaiting_staff")

    def test_cannot_leave_a_state_unable_to_take_an_inbound_email(self):
        self.refuse({"states": ["x"], "transitions": [{"from": "awaiting_staff", "event": "staff_resolved", "when": {}, "to": "x"}]}, "default transition|inbound_processed")

    def test_a_new_state_without_an_inbound_transition_is_refused(self):
        self.refuse({"states": ["x"], "expects": ["xx"],
                     "transitions": [{"from": "awaiting_staff", "event": "staff_released", "when": {"expects": "xx"}, "to": "x"}]},
                    "inbound_processed")

    def test_cannot_send_an_inbound_email_anywhere_but_awaiting_staff(self):
        self.refuse({"states": ["x"], "transitions": [{"from": "x", "event": "inbound_processed", "to": "resolved"}]}, "awaiting_staff")

    def test_cannot_set_the_initial_state(self):
        self.refuse({"initial": "resolved"}, "initial")

    def test_cannot_add_events(self):
        self.refuse({"transitions": [{"from": "open", "event": "auto_send", "to": "resolved"}]}, "event")

    def test_unknown_state_in_a_transition_is_refused(self):
        self.refuse({"transitions": [{"from": "ghost", "event": "staff_resolved", "to": "resolved"}]}, "ghost")

    def test_when_value_must_be_a_declared_expectation(self):
        self.refuse({"transitions": [{"from": "awaiting_staff", "event": "staff_released", "when": {"expects": "zzz"}, "to": "resolved"}]}, "expects")

    def test_timeouts_must_be_positive_whole_days_for_known_states(self):
        self.refuse({"timeouts_days": {"awaiting_member": 0}}, "timeout")
        self.refuse({"timeouts_days": {"ghost": 2}}, "ghost")

    def test_cannot_add_a_transition_out_of_a_terminal_state(self):
        self.refuse({"transitions": [{"from": "closed", "event": "staff_resolved", "to": "resolved"}]}, "terminal")

    def test_only_staff_closed_may_enter_a_terminal_state(self):
        self.refuse({"states": ["x"], "transitions": [
            {"from": "x", "event": "inbound_processed", "to": "awaiting_staff"},
            {"from": "x", "event": "staff_released", "to": "closed"}]}, "terminal")

    def test_staff_resolved_cannot_enter_a_terminal_state(self):
        self.refuse({"states": ["x"], "transitions": [
            {"from": "x", "event": "inbound_processed", "to": "awaiting_staff"},
            {"from": "x", "event": "staff_resolved", "to": "closed"}]}, "terminal")

    def test_awaiting_staff_cannot_go_straight_to_a_terminal_state(self):
        self.refuse({"transitions": [{"from": "awaiting_staff", "event": "staff_closed", "to": "closed"}]}, "awaiting_staff")

    def test_a_client_state_may_close_on_staff_closed(self):
        overlay = {"states": ["y"], "transitions": [
            {"from": "y", "event": "inbound_processed", "to": "awaiting_staff"},
            {"from": "y", "event": "staff_closed", "to": "closed"}]}
        out = lc.extend(BASE, overlay)
        self.assertEqual(lc.next_state(out, "y", "staff_closed"), "closed")

    def test_the_same_from_and_event_with_different_targets_is_ambiguous(self):
        self.refuse({"states": ["x"], "transitions": [
            {"from": "x", "event": "inbound_processed", "to": "awaiting_staff"},
            {"from": "x", "event": "staff_resolved", "to": "resolved"},
            {"from": "x", "event": "staff_resolved", "to": "awaiting_member"}]}, "ambiguous")

    def test_a_catch_all_release_beside_the_default_expectations_is_ambiguous(self):
        self.refuse({"transitions": [{"from": "awaiting_staff", "event": "staff_released", "to": "resolved"}]}, "ambiguous")

    def test_an_unconditional_transition_beside_a_conditional_one_is_ambiguous(self):
        self.refuse({"states": ["x"], "transitions": [
            {"from": "x", "event": "inbound_processed", "to": "awaiting_staff"},
            {"from": "x", "event": "staff_resolved", "to": "resolved"},
            {"from": "x", "event": "staff_resolved", "when": {"expects": "member"}, "to": "awaiting_member"}]}, "ambiguous")

    def test_an_exact_duplicate_transition_is_refused(self):
        t = {"from": "x", "event": "inbound_processed", "to": "awaiting_staff"}
        self.refuse({"states": ["x"], "transitions": [t, dict(t)]}, "duplicate")

    def test_an_overlay_must_be_a_mapping(self):
        self.refuse(["x"], "mapping")

    def test_a_transition_must_name_its_event(self):
        self.refuse({"transitions": [{"from": "open", "to": "resolved"}]}, "'event'")

    def test_a_transition_must_be_a_mapping(self):
        self.refuse({"transitions": ["open"]}, "mapping")

    def test_states_and_expects_must_be_lists_of_names(self):
        self.refuse({"states": "abc"}, "states")
        self.refuse({"states": None}, "states")
        self.refuse({"states": [""]}, "states")
        self.refuse({"expects": None}, "expects")

    def test_transitions_must_be_a_list(self):
        self.refuse({"transitions": None}, "transitions")

    def test_timeouts_days_must_be_a_mapping_of_state_names(self):
        self.refuse({"timeouts_days": ["awaiting_member"]}, "timeouts_days")

    def test_when_must_be_a_mapping_of_names(self):
        for when in ("expects", ["member"], {"expects": ["member"]}):
            self.refuse({"transitions": [{"from": "awaiting_staff", "event": "staff_released", "when": when, "to": "resolved"}]}, "'when'")

    def test_an_unknown_overlay_key_is_refused(self):
        self.refuse({"typo_key": 1}, "typo_key")


if __name__ == "__main__":
    unittest.main()
