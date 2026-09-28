"""HESTA house-style voice + per-intent reply guidance for the Writer (AI-011).

Paraphrased from the real outbound replies in the samples. This is the pilot's
"approved knowledge" — a small inline set, NOT a Bedrock Knowledge Base. The Writer
uses ``style_guide()`` for voice and ``snippet_for(intent_id)`` for the intent-specific
next steps. Nothing here promises a regulated outcome.
"""

from __future__ import annotations

GREETING = "Thank you for contacting HESTA."
SIGNOFF = "We're here to help,\nThe team at HESTA"
# Shortened footer — the full legal footer is appended by HESTA's mail system.
LEGAL_FOOTER = (
    "Issued by H.E.S.T. Australia Ltd ABN 66 006 818 695 AFSL 235249, Trustee of HESTA "
    "ABN 64 971 749 321. This information is general only and does not consider your "
    "objectives, financial situation or needs."
)

# The identity-verification ask HESTA uses before acting on a request.
IDENTITY_VERIFICATION_REQUEST = (
    "For us to proceed with your request, please reply to this email with:\n"
    "- Member number\n"
    "- Full name (including any middle names)\n"
    "- Date of birth\n"
    "- The address we have on our records"
)

# Per-intent next-steps guidance (safe, non-committal, mirrors the sample replies).
_SNIPPETS: dict[str, str] = {
    "death_benefit_nomination": (
        "You can nominate who receives your super via a binding or non-binding nomination. "
        "A binding nomination must be made on the Binding Death Nomination form; a non-binding "
        "nomination can be updated any time in Member Online under Personal details. If a form was "
        "attached, confirm it has been received and is being reviewed; if it could not be opened, ask "
        "the member to resend it as a PDF in a new email with their member number in the subject line."
    ),
    "withdrawal_benefit_payment": (
        "Explain that withdrawals/benefit payments require identity verification and a completed "
        "application, and that applying via Member Online is the most reliable channel. If checking "
        "the status of an existing application, confirm what can be seen on file and the expected "
        "processing time. Do not confirm approval or amounts."
    ),
    "change_of_details": (
        "For updating contact details (mobile, email, address), confirm the change can be actioned once "
        "identity is verified, and that Member Online access should be restored afterwards. Note any "
        "accessibility preference the member has stated (e.g. email preferred)."
    ),
    "departing_australia_payment": (
        "For a Departing Australia Superannuation Payment (DASP), explain it applies to temporary "
        "residents who have permanently left Australia, and point to the ATO DASP process. If the member "
        "is in New Zealand, mention the KiwiSaver transfer option. Confirm identity before proceeding."
    ),
    "financial_hardship": (
        "Acknowledge the member's situation with care. Explain that financial hardship early release has "
        "eligibility criteria and requires an application with supporting evidence (e.g. a recent bank "
        "statement), and outline how to submit documents (PDF attachment, member number in the subject). "
        "Do not confirm eligibility, amounts, or timing — a HESTA specialist assesses the application."
    ),
    "family_law_split": (
        "For family law superannuation split correspondence (often from a solicitor), confirm the request "
        "will be directed to the HESTA family law team, and list the member identification / authority "
        "details required before information can be released. Note urgency if a court date is mentioned."
    ),
    "notice_of_intent_tax_deduction": (
        "Explain how to lodge a Notice of Intent (NOI) to claim a tax deduction for a personal contribution: "
        "complete the ATO NOI form and return it to HESTA; funds are then allocated to the nominated "
        "contribution type. If the member says a form was already sent, confirm receipt if it can be seen."
    ),
    "rollover_transfer_combine": (
        "For rollovers/transfers/combining accounts, explain how to consolidate into HESTA (e.g. via Member "
        "Online or the relevant form). If a transfer is not yet showing, note typical processing times and "
        "ask the member to check Member Online, escalating if it is materially overdue."
    ),
    "other_unknown": (
        "Acknowledge the enquiry, ask a brief clarifying question to identify what the member needs, and "
        "confirm identity if any account action may be required."
    ),
}


# Compliant reply when a member asks for PERSONAL financial advice — HESTA must not provide it.
PERSONAL_ADVICE_DECLINE = (
    "Thanks for reaching out. We want to help, but we're not able to provide you with personal "
    "financial advice — such as which option or product is right for your individual circumstances "
    "— over email.\n\n"
    "What we can do is give you general information about your options and how they work, and point "
    "you to where you can get personal advice. HESTA members can access personal financial advice "
    "through HESTA's advice services, or you may wish to speak with a licensed financial adviser.\n\n"
    "If you'd like, let us know what general information would be helpful and we'll be glad to assist."
)


def style_guide() -> str:
    return (
        f"HESTA voice: warm, plain-English, supportive. Open with '{GREETING}' then 'Hi <first name>,'. "
        f"Close with:\n{SIGNOFF}\n"
        "Keep it concise. Never promise or confirm a regulated outcome (approval, eligibility, amount, or "
        "timing). If identity is not verified, ask for the verification details before actioning anything."
    )


def snippet_for(intent_id: str) -> str:
    return _SNIPPETS.get(intent_id, _SNIPPETS["other_unknown"])
