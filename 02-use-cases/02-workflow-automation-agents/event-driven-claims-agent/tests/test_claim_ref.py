"""core/claim_ref.py: minting, reading and resolving a claim reference.

Run:  ./.venv/bin/python3 -m unittest tests.test_claim_ref -v
"""

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app" / "04_hesta_member_email"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from core import claim_ref as cr  # noqa: E402

A = "customer.a@example.com"


def mail(subject="Hello", sender=A, mid="<m@x>", in_reply_to="", references=()):
    return cr.InboundMail(message_id=mid, in_reply_to=in_reply_to, references=tuple(references), subject=subject, sender=sender)


def resolve(m, claims=None, messages=None):
    claims, messages = claims or {}, messages or {}
    return cr.resolve(m, sender_of_ref=claims.get, ref_of_message_id=messages.get)


class MintTests(unittest.TestCase):
    def test_mint_format(self):
        self.assertEqual(cr.mint_claim_ref(rand=lambda: "1a2b3c4d"), "CLR-1A2B3C4D")

    def test_minted_refs_differ(self):
        self.assertNotEqual(cr.mint_claim_ref(), cr.mint_claim_ref())

    def test_prefix_is_not_createclaims(self):
        self.assertFalse(cr.mint_claim_ref().startswith("CLM-"))


class SubjectTests(unittest.TestCase):
    def test_reads_refs_case_insensitively_and_dedupes(self):
        self.assertEqual(cr.refs_in_subject("Re: [clr-1a2b3c4d] x CLR-1A2B3C4D"), ["CLR-1A2B3C4D"])

    def test_ignores_near_misses(self):
        self.assertEqual(cr.refs_in_subject("CLR-1A2B3C4 CLM-1A2B3C4D CLR-GGGGGGGG"), [])

    def test_normalise_address(self):
        self.assertEqual(cr.normalise_address('"Ann A" <Ann.A@Example.com>'), "ann.a@example.com")
        self.assertEqual(cr.normalise_address("ann.a@example.com"), "ann.a@example.com")


class ResolveTests(unittest.TestCase):
    def test_first_contact_is_a_new_claim(self):
        self.assertEqual(resolve(mail("Question about my claim")).kind, "new")

    def test_subject_reference_attaches_to_the_claim(self):
        r = resolve(mail("Re: [CLR-1A2B3C4D] x"), claims={"CLR-1A2B3C4D": A})
        self.assertEqual((r.kind, r.claim_ref), ("existing", "CLR-1A2B3C4D"))

    def test_header_alone_attaches(self):
        r = resolve(mail("Re: x", in_reply_to="<d1@x>"), messages={"<d1@x>": "CLR-1A2B3C4D"}, claims={"CLR-1A2B3C4D": A})
        self.assertEqual((r.kind, r.claim_ref), ("existing", "CLR-1A2B3C4D"))

    def test_references_header_is_used_when_in_reply_to_is_unknown(self):
        r = resolve(mail("Re: x", in_reply_to="<zz@x>", references=["<zz@x>", "<d1@x>"]),
                    messages={"<d1@x>": "CLR-1A2B3C4D"}, claims={"CLR-1A2B3C4D": A})
        self.assertEqual(r.claim_ref, "CLR-1A2B3C4D")

    def test_subject_and_headers_agreeing_is_fine(self):
        r = resolve(mail("Re: [CLR-1A2B3C4D] x", in_reply_to="<d1@x>"), messages={"<d1@x>": "CLR-1A2B3C4D"}, claims={"CLR-1A2B3C4D": A})
        self.assertEqual(r.kind, "existing")

    def test_disagreeing_signals_go_to_a_person(self):
        r = resolve(mail("Re: [CLR-AAAAAAAA] x", in_reply_to="<d1@x>"), messages={"<d1@x>": "CLR-BBBBBBBB"},
                    claims={"CLR-AAAAAAAA": A, "CLR-BBBBBBBB": A})
        self.assertEqual(r.kind, "needs_person")
        self.assertIn("disagree", r.reason)

    def test_two_references_in_one_subject_go_to_a_person(self):
        r = resolve(mail("[CLR-AAAAAAAA] [CLR-BBBBBBBB]"), claims={"CLR-AAAAAAAA": A, "CLR-BBBBBBBB": A})
        self.assertEqual(r.kind, "needs_person")

    def test_reply_with_no_reference_goes_to_a_person_not_a_new_claim(self):
        cases = (mail("Re: Question"), mail("Fwd: Question"), mail("Question", in_reply_to="<unknown@x>"), mail("Question", references=["<u@x>"]))
        for m in cases:
            self.assertEqual(resolve(m).kind, "needs_person", m.subject)

    def test_unknown_reference_goes_to_a_person(self):
        self.assertEqual(resolve(mail("Re: [CLR-DEADBEEF] x")).kind, "needs_person")

    def test_reference_from_a_different_sender_never_attaches(self):
        r = resolve(mail("Re: [CLR-1A2B3C4D] x", sender="someone.else@example.com"), claims={"CLR-1A2B3C4D": A})
        self.assertEqual(r.kind, "needs_person")
        self.assertIn("sender", r.reason)

    def test_sender_comparison_ignores_case_and_display_name(self):
        r = resolve(mail("Re: [CLR-1A2B3C4D] x", sender='"A" <Customer.A@Example.com>'), claims={"CLR-1A2B3C4D": A})
        self.assertEqual(r.kind, "existing")

    def test_two_open_claims_pick_the_one_named(self):
        claims = {"CLR-0000OLD1": A, "CLR-1111NEW2": A}
        self.assertEqual(resolve(mail("Re: [CLR-0000AAAA] x"), claims={"CLR-0000AAAA": A, "CLR-1111BBBB": A}).claim_ref, "CLR-0000AAAA")
        self.assertEqual(len(claims), 2)


if __name__ == "__main__":
    unittest.main()
