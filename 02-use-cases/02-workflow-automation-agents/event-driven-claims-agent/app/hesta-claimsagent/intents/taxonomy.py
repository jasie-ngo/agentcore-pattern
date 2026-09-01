"""The 8 HESTA member-request intents + signals, used to build the AI-001 prompt.

Folder codes in ``hesta/`` are the ground-truth labels; ``id`` is the machine
value the pipeline uses. ``regulated`` drives the human-in-the-loop gate, and
``expected_attachment`` feeds AI-004's expectation check.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Intent:
    id: str
    code: str
    name: str
    regulated: bool
    signals: list[str]
    example: str
    expected_attachment: str = "none"


INTENTS: list[Intent] = [
    Intent(
        id="death_benefit_nomination",
        code="BDBN",
        name="Death Benefit / Binding Death Benefit Nomination",
        regulated=True,
        signals=[
            "binding death nomination / beneficiary nomination",
            "nominate or change who receives my super when I die",
            "add / remove / update beneficiaries",
            "attached Binding Death Nomination form",
        ],
        example="Please find attached my completed and signed Binding Death Nomination form for member [MEMBER NUMBER].",
        expected_attachment="Binding Death Nomination form",
    ),
    Intent(
        id="withdrawal_benefit_payment",
        code="BP",
        name="Withdrawal / Benefit Payment",
        regulated=True,
        signals=[
            "withdraw / access my super",
            "benefit payment, lump sum",
            "reason-for-enquiry: Accessing super",
            "status of a withdrawal application already submitted",
        ],
        example="I would like to know if you received my application to withdraw some money from my account.",
        expected_attachment="none",
    ),
    Intent(
        id="change_of_details",
        code="COD",
        name="Change of Personal Details",
        regulated=False,
        signals=[
            "update / change mobile number, email, or address",
            "can't log in / restore Member Online access",
            "reason-for-enquiry: Updating my account details",
        ],
        example="My mobile number has been updated. Could you please update my details so I can log in to my account?",
        expected_attachment="none",
    ),
    Intent(
        id="departing_australia_payment",
        code="DASP",
        name="Departing Australia Superannuation Payment (DASP)",
        regulated=True,
        signals=[
            "left or leaving Australia permanently",
            "temporary resident claiming super after departure",
            "living overseas / NZ / KiwiSaver transfer",
        ],
        example="I would like to withdraw my super as I live in New Zealand. Can you advise the process?",
        expected_attachment="none",
    ),
    Intent(
        id="financial_hardship",
        code="FH",
        name="Financial Hardship",
        regulated=True,
        signals=[
            "financial hardship / severe financial hardship",
            "early release to pay bills; $10,000 hardship payment",
            "can't afford rent / bills / medical costs",
            "supporting evidence such as a bank statement",
        ],
        example="I am asking about the $10,000 financial hardship withdrawal to help pay my accumulating bills.",
        expected_attachment="bank statement / evidence of hardship",
    ),
    Intent(
        id="family_law_split",
        code="FLS",
        name="Family Law Split",
        regulated=True,
        signals=[
            "family law superannuation split",
            "sender is a solicitor / law firm (professional correspondence)",
            "procedural fairness, court order, Form 6, subpoena",
        ],
        example="We refer to the above matter and confirm our office is yet to receive a response regarding the family law split.",
        expected_attachment="court order / legal documents",
    ),
    Intent(
        id="notice_of_intent_tax_deduction",
        code="NOI",
        name="Notice of Intent to Claim a Tax Deduction",
        regulated=True,
        signals=[
            "notice of intent (NOI) to claim a tax deduction",
            "claim a deduction for a personal / concessional contribution",
            "ATO notice of intent form",
        ],
        example="How do I lodge a Notice of Intent to claim a tax deduction for my personal contribution?",
        expected_attachment="ATO Notice of Intent form",
    ),
    Intent(
        id="rollover_transfer_combine",
        code="RTC",
        name="Rollover / Transfer / Combine Accounts",
        regulated=False,
        signals=[
            "roll over / transfer / combine / consolidate super",
            "move another fund (e.g. Rest, KiwiSaver) into HESTA",
            "transferred funds not yet showing in my account",
        ],
        example="I transferred my Rest super to HESTA but it is not appearing in my account yet.",
        expected_attachment="none",
    ),
]

OTHER_UNKNOWN = "other_unknown"

_BY_ID = {i.id: i for i in INTENTS}
VALID_IDS: list[str] = [i.id for i in INTENTS] + [OTHER_UNKNOWN]
REGULATED_IDS: set[str] = {i.id for i in INTENTS if i.regulated}


def is_regulated(intent_id: str) -> bool:
    return intent_id in REGULATED_IDS


def expected_attachment(intent_id: str) -> str:
    intent = _BY_ID.get(intent_id)
    return intent.expected_attachment if intent else "none"


def name_for(intent_id: str) -> str:
    if intent_id == OTHER_UNKNOWN:
        return "Other / Unknown"
    intent = _BY_ID.get(intent_id)
    return intent.name if intent else intent_id


def render_for_prompt() -> str:
    """Render the taxonomy as few-shot guidance for the Intent Identifier."""
    lines = []
    for i in INTENTS:
        reg = "REGULATED" if i.regulated else "non-regulated"
        signals = "; ".join(i.signals)
        lines.append(
            f"- id: {i.id}  ({i.code}, {reg})\n"
            f"    name: {i.name}\n"
            f"    signals: {signals}\n"
            f'    example: "{i.example}"'
        )
    lines.append(
        f"- id: {OTHER_UNKNOWN}  (fallback)\n"
        f"    name: Other / Unknown\n"
        f"    signals: none of the above, ambiguous, or a general enquiry"
    )
    return "\n".join(lines)
