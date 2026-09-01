#!/usr/bin/env python3
"""Generate sample inbound "emails" (one .txt per seed member) for testing the HESTA agent.

Writes to hesta/sample-emails/. Each file corresponds to a seed member in
scripts/seed_hesta_members.py and is crafted for that member's intent scenario, so:
  - the sender/email matches the member record (AI-003 → verified), and
  - the member number appears as "member number <n>" (or the contact-form field) so the
    normalizer extracts it reliably (not a stray dollar amount).

Two realistic shapes are produced (matching the real HESTA samples):
  - contact_form : "You've received a new form based mail … Values: …"
  - direct       : From:/Subject:/Date: headers + body (some with [EXTERNAL] warning banner,
                   attachment markers, quoted thread and legal footer to test stripping)

FLS emails come from a solicitor (third party), so the sender won't match the member —
exercising sender_type=solicitor and the not-verified → human-review path.

Run:  python3 scripts/generate_sample_emails.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from seed_hesta_members import MEMBERS  # noqa: E402

BY_NUM = {m["policy_number"]: m for m in MEMBERS}

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "hesta", "sample-emails")

WARNING_BANNER = (
    "WARNING:\n"
    "This email originated from outside of HESTA and was sent from a non-approved client "
    "domain. DO NOT click links or open attachments unless you recognise the sender and know "
    "the content is safe.\n"
)
LEGAL_FOOTER = (
    "\nIssued by H.E.S.T. Australia Ltd ABN 66 006 818 695 AFSL 235249, the Trustee of HESTA "
    "ABN 64 971 749 321. This information is of a general nature.\n"
)

# policy_number -> spec. slug/subject/reason are for the file + headers; body is the member's words.
SPECS = {
    # ── BDBN ────────────────────────────────────────────────────────────────
    "60010001": dict(slug="binding_nomination_form", channel="direct", phone="0400 111 001",
                     subject="[EXTERNAL] Binding Death Nomination for member number 60010001",
                     banner=True, footer=True, attachments=2,
                     body="Good afternoon,\n\nPlease find attached my completed and signed Binding Death "
                          "Nomination form updating my nomination to my spouse. Member number 60010001.\n\n"
                          "Please confirm receipt of this form by replying to this email.\n\nKind regards,\nSarah Thompson"),
    "60010002": dict(slug="view_and_change_nomination", channel="contact_form", reason="My online account",
                     phone="0400 111 002",
                     message="Hi, I want to see who I have nominated on my account and change my non-binding "
                             "nomination to add my daughter as a 50% beneficiary. Please advise how."),
    "60010003": dict(slug="how_to_make_binding_nomination", channel="contact_form", reason="Other",
                     phone="0400 111 003",
                     message="Hello, I don't currently have any beneficiary listed. How do I make a binding "
                             "death benefit nomination? Can you send me the form?"),
    "60010004": dict(slug="lapsed_nomination_renewal", channel="direct", phone="0400 111 004",
                     subject="Binding nomination lapsed - how do I renew?",
                     body="Hi,\n\nI received a notice that my binding death benefit nomination has lapsed. "
                          "I'd like to renew it with the same beneficiary. What do I need to do? "
                          "Member number 60010004.\n\nThanks,\nDavid Okafor"),
    "60010005": dict(slug="remove_beneficiaries_nominate_estate", channel="contact_form", reason="Other",
                     phone="0400 111 005",
                     message="I would like to remove the beneficiaries currently on my account and instead "
                             "nominate my legal personal representative (my estate). Please let me know the steps."),

    # ── BP ──────────────────────────────────────────────────────────────────
    "60020001": dict(slug="lump_sum_withdrawal_over65", channel="direct", phone="0400 222 001",
                     subject="Request to withdraw a lump sum",
                     body="Dear HESTA,\n\nI am over 65 and would like to withdraw a lump sum of $40,000 from my "
                          "income stream to pay for home renovations. Could you advise the process please? "
                          "Member number 60020001.\n\nRegards,\nRobert Hayes"),
    "60020002": dict(slug="retired_access_super", channel="contact_form", reason="Accessing super",
                     phone="0400 222 002",
                     message="I have permanently retired and would like to access some of my super. "
                             "What is the process and how long does it take?"),
    "60020003": dict(slug="early_withdrawal_not_eligible", channel="contact_form", reason="Accessing super",
                     phone="0400 222 003",
                     message="Hi, I need to withdraw some of my super to pay off credit card debt. I'm 34 and "
                             "still working. Can I access it early? Member number 60020003."),
    "60020004": dict(slug="withdrawal_enquiry_inactive", channel="direct", phone="0400 222 004",
                     subject="Following up on my withdrawal",
                     body="Hello,\n\nI'd like to check on withdrawing from my account. I haven't used it in a "
                          "while. Member number 60020004. Please let me know what I need to provide.\n\nSusan Beckett"),
    "60020005": dict(slug="withdrawal_status_check", channel="contact_form", reason="Accessing super",
                     phone="0400 222 005",
                     message="I submitted an application to withdraw some money about two weeks ago and I still "
                             "haven't heard back. Has it been processed yet? Member number 60020005."),

    # ── COD ─────────────────────────────────────────────────────────────────
    "60030001": dict(slug="update_mobile_no_access", channel="contact_form", reason="Updating my account details",
                     phone="0400 333 001",
                     message="Hi there, my mobile number has changed. I tried to update it in the portal but it "
                             "says I need to call. Could you please update my number so I can log in again?"),
    "60030002": dict(slug="update_mobile_deaf_prefers_email", channel="contact_form",
                     reason="Updating my account details", phone="0400 333 002",
                     message="I need to change my mobile number. I am Deaf and prefer to use email as my "
                             "contact method rather than phone. Please update my details."),
    "60030003": dict(slug="update_address_moved", channel="direct", phone="0400 333 003",
                     subject="Change of address",
                     body="Hi,\n\nI've moved house and need to update the address on my account. My new address "
                          "is 3 Vale St, Newcastle NSW 2300. Member number 60030003.\n\nThanks,\nPeter Walsh"),
    "60030004": dict(slug="restore_access_new_mobile", channel="contact_form",
                     reason="Updating my account details", phone="0400 333 004",
                     message="Please put my new mobile number above in the system as I'm unable to access my account."),
    "60030005": dict(slug="change_email_on_file", channel="direct", phone="0400 333 005",
                     subject="Update email address",
                     body="Hello,\n\nPlease update the email address on my account to this one. Member number "
                          "60030005. Let me know if you need anything else to action this.\n\nHassan Ali"),

    # ── DASP ────────────────────────────────────────────────────────────────
    "60040001": dict(slug="claim_super_left_australia_uk", channel="direct", phone="+44 20 7946 0001",
                     subject="[EXTERNAL] Claiming super after leaving Australia", banner=True,
                     body="Hello,\n\nI was on a working holiday visa and have now permanently left Australia and "
                          "returned to the UK. I would like to claim my super. What is the process? "
                          "Member number 60040001.\n\nKind regards,\nEmma Wilson"),
    "60040002": dict(slug="dasp_temporary_resident_japan", channel="contact_form", reason="Accessing super",
                     phone="+81 3 1234 0002",
                     message="I was a temporary resident and have departed Australia for Japan. How do I apply "
                             "for the Departing Australia Superannuation Payment? Member number 60040002."),
    "60040003": dict(slug="dasp_process_spain", channel="direct", phone="+34 91 123 0003",
                     subject="Super after departing Australia",
                     body="Hi,\n\nI've left Australia permanently and now live in Spain. Can you advise how I "
                          "withdraw my super as a former temporary resident? Member number 60040003.\n\nMaria Gonzalez"),
    "60040004": dict(slug="nz_kiwisaver_or_withdraw", channel="contact_form", reason="Accessing super",
                     phone="+64 9 123 0004",
                     message="I have moved to New Zealand. Can I transfer my HESTA super to KiwiSaver, or should "
                             "I withdraw it? Member number 60040004. Please advise the options."),
    "60040005": dict(slug="dasp_followup_already_claimed", channel="direct", phone="+91 22 1234 0005",
                     subject="Following up on my DASP claim",
                     body="Hello,\n\nI submitted a claim for my departing Australia super payment some time ago "
                          "and want to confirm it was finalised. Member number 60040005.\n\nRegards,\nPriya Sharma"),

    # ── FH ──────────────────────────────────────────────────────────────────
    "60050001": dict(slug="financial_hardship_rent", channel="contact_form", reason="Accessing super",
                     phone="0400 555 001",
                     message="I'm in financial hardship and struggling to pay my rent. I'd like to apply for an "
                             "early release of my super. What are the eligibility criteria and how do I apply? "
                             "Member number 60050001."),
    "60050002": dict(slug="hardship_timing_eofy", channel="contact_form", reason="Accessing super",
                     phone="0400 555 002",
                     message="I received a $10,000 financial hardship payment last year. When can I apply again, "
                             "and will I be able to before the end of the financial year? Member number 60050002."),
    "60050003": dict(slug="hardship_60plus_working", channel="contact_form", reason="Accessing super",
                     phone="0400 555 003",
                     message="I'm over 60, Aboriginal, and still working full time but my bills are piling up. "
                             "Can I access $10,000 under financial hardship? Member number 60050003."),
    "60050004": dict(slug="hardship_document_upload", channel="direct", phone="0400 555 004",
                     subject="[EXTERNAL] Where do I upload my bank statement?", banner=True, attachments=1,
                     body="Hi,\n\nI'm applying for a financial hardship withdrawal and you asked for a bank "
                          "statement. I can't find where to re-upload it. I've attached it here. "
                          "Member number 60050004.\n\nThanks,\nFatima Noor"),
    "60050005": dict(slug="severe_hardship_income_support", channel="contact_form", reason="Accessing super",
                     phone="0400 555 005",
                     message="I've been on income support for over 6 months and I'm in severe financial hardship. "
                             "I need to withdraw from my super as soon as possible. Member number 60050005."),

    # ── FLS (solicitor / third party) ────────────────────────────────────────
    "60060001": dict(slug="family_law_information_request", channel="direct", solicitor=True,
                     from_name="Meredith Cole", from_email="m.cole@example-legal.com.au",
                     subject="[EXTERNAL] Family law - request for information", banner=True, footer=True,
                     body="Dear Sir/Madam,\n\nWe act for a party to family law proceedings and require "
                          "information about the superannuation interest of your member Rebecca Coleman, "
                          "member number 60060001, under the Family Law Act. Please advise the requirements to "
                          "proceed.\n\nYours faithfully,\nCole & Associates"),
    "60060002": dict(slug="family_law_split_chase", channel="direct", solicitor=True,
                     from_name="Daniel Pryor", from_email="d.pryor@example-legal.com.au",
                     subject="[EXTERNAL] Superannuation split - follow up", banner=True,
                     body="Dear Sir/Madam,\n\nWe refer to the above matter and note our office is yet to receive "
                          "a response regarding the superannuation split for your member Mark Ellison, member "
                          "number 60060002. Please treat as urgent.\n\nRegards,\nPryor Legal"),
    "60060003": dict(slug="family_law_urgent_court_date", channel="direct", solicitor=True,
                     from_name="Amrita Singh", from_email="a.singh@example-legal.com.au",
                     subject="[EXTERNAL] URGENT - family law split, court date approaching", banner=True,
                     body="Dear Sir/Madam,\n\nWe act in a matter with a court date on 15 October 2026 and require "
                          "the procedural fairness information for the super interest of Sophie Delacroix, member "
                          "number 60060003. Please respond as a priority.\n\nSingh Family Law"),
    "60060004": dict(slug="family_law_procedural_fairness", channel="direct", solicitor=True,
                     from_name="Tom Beckwith", from_email="t.beckwith@example-legal.com.au",
                     subject="[EXTERNAL] Procedural fairness request", banner=True,
                     body="Dear Sir/Madam,\n\nWe request procedural fairness documentation in relation to the "
                          "superannuation of Andrew Blackwood, member number 60060004, for a family law property "
                          "settlement.\n\nYours faithfully,\nBeckwith Lawyers"),
    "60060005": dict(slug="member_asks_family_law_process", channel="contact_form", reason="Other",
                     phone="0400 666 005",
                     message="My ex-partner's lawyer has contacted me about splitting my super in our divorce. "
                             "What is the process from HESTA's side and what do you need from me? "
                             "Member number 60060005."),

    # ── NOI ─────────────────────────────────────────────────────────────────
    "60070001": dict(slug="how_to_lodge_noi", channel="contact_form", reason="Other", phone="0400 777 001",
                     message="Hi, I made a $15,000 personal contribution this year and want to claim a tax "
                             "deduction. How do I lodge a Notice of Intent? Member number 60070001."),
    "60070002": dict(slug="request_noi_form", channel="direct", phone="0400 777 002",
                     subject="Notice of Intent form request",
                     body="Hello,\n\nI've contributed $27,500 in personal (concessional) contributions and would "
                          "like the Notice of Intent form to claim my deduction. Member number 60070002.\n\n"
                          "Thanks,\nOlivia Bennett"),
    "60070003": dict(slug="noi_bring_forward", channel="contact_form", reason="Other", phone="0400 777 003",
                     message="I've made a $50,000 personal contribution using the bring-forward. I need the NOI "
                             "documentation to claim the deduction on my tax return. Member number 60070003."),
    "60070004": dict(slug="confirm_noi_received", channel="direct", phone="0400 777 004",
                     subject="Re: Notice of Intent - please confirm receipt", banner=True, footer=True,
                     body="Hi,\n\nThanks - I have already sent my Notice of Intent form to you. Can you please "
                          "confirm it was received? Member number 60070004.\n\nKind regards,\nKaren Mitchell\n\n"
                          "From: HESTA <hesta@hesta.com.au>\nSent: 28 May 2026 10:14 AM\nSubject: Notice of Intent\n"
                          "Thank you for contacting HESTA. You can download an NOI form here..."),
    "60070005": dict(slug="noi_submission_issue", channel="contact_form", reason="Other", phone="0400 777 005",
                     message="I'm trying to submit my Notice of Intent but the form won't go through. Is the "
                             "issue with the online portal or should I send a paper copy? Member number 60070005."),

    # ── RTC ─────────────────────────────────────────────────────────────────
    "60080001": dict(slug="rollover_rest_in_progress", channel="contact_form", reason="Other", phone="0400 888 001",
                     message="Hi, I'm rolling over my Rest super into my HESTA account. Can you confirm it's in "
                             "progress and how long it usually takes? Member number 60080001."),
    "60080002": dict(slug="combine_rest_into_hesta", channel="contact_form", reason="Accessing super",
                     phone="0400 888 002",
                     message="I'd like to combine my Rest super into my HESTA account to have everything in one "
                             "place. What do I need to do? Member number 60080002."),
    "60080003": dict(slug="transfer_status_processing", channel="direct", phone="0400 888 003",
                     subject="Transfer status",
                     body="Hello,\n\nI submitted a transfer into HESTA on 2 May 2026. Can you tell me the status "
                          "and whether it has completed? Member number 60080003.\n\nRegards,\nEleanor Page"),
    "60080004": dict(slug="consolidate_two_funds", channel="contact_form", reason="Other", phone="0400 888 004",
                     message="I have super with AustralianSuper and HostPlus that I want to consolidate into HESTA. "
                             "Please advise how to combine them. Member number 60080004."),
    "60080005": dict(slug="transfer_not_showing", channel="direct", phone="0400 888 005",
                     subject="[EXTERNAL] Transferred super not showing", banner=True,
                     body="Hi,\n\nI transferred my super from another fund to HESTA nearly a week ago and it's "
                          "still not showing in my account. myGov shows it has left the other fund. Can you check? "
                          "Member number 60080005.\n\nThanks,\nIsabella Romano"),
}


def _contact_form(member, spec) -> str:
    return (
        "You've received a new form based mail from "
        "https://www.hesta.com.au/content/hesta/about-us/contact-us.html\n"
        "Values:\n"
        "enquiry-sent-from : A member\n"
        f"email-address : {member['email']}\n"
        f"member-number : {member['policy_number']}\n"
        f"name : {member['holder_name']}\n"
        f"phone : {spec.get('phone', '')}\n"
        f"reason-for-enquiry : {spec['reason']}\n"
        f"message : {spec['message']}\n"
    )


def _direct(member, spec) -> str:
    from_name = spec.get("from_name", member["holder_name"])
    from_email = spec.get("from_email", member["email"])
    parts = []
    if spec.get("banner"):
        parts.append(WARNING_BANNER)
    parts.append(
        f"From: {from_name} <{from_email}>\n"
        "To: HESTA <hesta@hesta.com.au>\n"
        f"Subject: {spec['subject']}\n"
        "Date: 29 May 2026 09:12 AM\n"
    )
    body = spec["body"]
    if spec.get("attachments"):
        body += "\n" + "\n".join("[ATTACHMENT FILENAME]" for _ in range(spec["attachments"]))
    parts.append(body)
    if spec.get("footer"):
        parts.append(LEGAL_FOOTER)
    return "\n".join(parts) + "\n"


def main() -> None:
    out = os.path.abspath(OUT_DIR)
    os.makedirs(out, exist_ok=True)
    written = 0
    for num, spec in SPECS.items():
        member = BY_NUM[num]
        content = _contact_form(member, spec) if spec["channel"] == "contact_form" else _direct(member, spec)
        fname = f"{member['test_scenario']}_{num}_{spec['slug']}.txt"
        with open(os.path.join(out, fname), "w", encoding="utf-8") as fh:
            fh.write(content)
        written += 1
    print(f"✅ Wrote {written} sample emails to {out}")
    missing = set(BY_NUM) - set(SPECS)
    if missing:
        print(f"⚠️ No spec for members: {sorted(missing)}")


if __name__ == "__main__":
    main()
