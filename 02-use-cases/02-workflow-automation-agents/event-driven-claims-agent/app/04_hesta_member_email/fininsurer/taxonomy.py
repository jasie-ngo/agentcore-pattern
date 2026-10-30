"""The illustrative finance-insurer intents and their signals: the pipeline's shared taxonomy.

ILLUSTRATIVE: made up for a pre-sale demo, not a real insurer's. Replace with the insurer's own.

``human_decision`` marks an intent only a person may decide (a claim, hardship relief, a cancellation, a complaint): the routing gate
always escalates it. ``expected_attachment`` feeds the attachment check.

``render_for_prompt()`` produces the taxonomy block inlined in ``agents.intent_identifier.system_prompt`` in the overlay
(``clients/fininsurer/pipeline/pipeline.yaml``). ``tests/test_fininsurer.py`` checks the two agree: run it after editing ``INTENTS``
and paste the output back into the overlay.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    id: str
    code: str
    name: str
    human_decision: bool
    signals: list[str]
    example: str
    expected_attachment: str = "none"


HARDSHIP_EVIDENCE = "evidence of financial hardship (e.g. income statement or benefit letter)"

INTENTS: list[Intent] = [
    Intent(
        id="hardship_premium_relief",
        code="HPR",
        name="Financial Hardship: Premium Relief",
        human_decision=True,
        signals=[
            "cannot afford the premiums; behind on premium payments",
            "asks for a payment plan, a pause, or reduced cover because of money trouble",
            "lost job or income, redundancy, illness or a family crisis affecting payments",
            "attached evidence such as an income statement or a benefit letter",
        ],
        example="I lost my job last month and can't keep up with my premiums. Is there a payment arrangement I can go on?",
        expected_attachment=HARDSHIP_EVIDENCE,
    ),
    Intent(
        id="hardship_claim_support",
        code="HCS",
        name="Financial Hardship: Claim Support",
        human_decision=True,
        signals=[
            "a claim is already lodged or pending and the person is under financial pressure",
            "asks for an urgent, partial or early payment while the claim is assessed",
            "cannot pay for repairs, rent or bills until the claim is paid",
            "attached evidence such as a bank statement",
        ],
        example="My claim is still pending and I can't pay for the repairs. Can part of it be paid sooner?",
        expected_attachment=HARDSHIP_EVIDENCE,
    ),
    Intent(
        id="claim_lodgement",
        code="CL",
        name="Lodge a Claim",
        human_decision=True,
        signals=[
            "wants to make or lodge a new claim",
            "describes an event (damage, theft, accident, illness) and asks what to do",
            "attached claim form, photos or invoices",
        ],
        example="A storm damaged my roof on Friday. How do I lodge a claim?",
        expected_attachment="claim form and supporting documents",
    ),
    Intent(
        id="claim_status",
        code="CS",
        name="Claim Status",
        human_decision=False,
        signals=[
            "asks for an update on a claim already lodged",
            "has the claim been received or processed; how long will it take",
            "chasing a reply",
        ],
        example="I lodged a claim two weeks ago and haven't heard anything. Can you tell me where it's up to?",
    ),
    Intent(
        id="cancellation_request",
        code="CAN",
        name="Cancel, Reduce or Pause Cover",
        human_decision=True,
        signals=[
            "asks to cancel, reduce or pause a policy",
            "asks how cancellation works or what it would cost",
        ],
        example="Please cancel my policy from the end of this month.",
    ),
    Intent(
        id="complaint_dispute",
        code="CD",
        name="Complaint or Dispute",
        human_decision=True,
        signals=[
            "says they are unhappy with a decision, a delay or the service",
            "disputes a claim outcome, a premium or a fee",
            "mentions a formal complaint, the ombudsman or a regulator",
        ],
        example="I'm disappointed with how my claim was handled and I want to make a formal complaint.",
    ),
    Intent(
        id="change_of_details",
        code="COD",
        name="Change of Personal Details",
        human_decision=False,
        signals=[
            "update or change a phone number or postal address",
            "can't log in or needs online access restored",
        ],
        example="I've moved house. Could you please update the address on my policy?",
    ),
    Intent(
        id="policy_enquiry",
        code="PE",
        name="General Policy Enquiry",
        human_decision=False,
        signals=[
            "asks what a policy covers, when it renews, or how a process works",
            "general information with no request for action or advice",
        ],
        example="When does my policy renew, and what does the excess apply to?",
    ),
]

OTHER_UNKNOWN = "other_unknown"

_BY_ID = {i.id: i for i in INTENTS}
VALID_IDS: list[str] = [i.id for i in INTENTS] + [OTHER_UNKNOWN]
HUMAN_DECISION_IDS: set[str] = {i.id for i in INTENTS if i.human_decision}
HARDSHIP_IDS: set[str] = {"hardship_premium_relief", "hardship_claim_support"}


def is_human_decision(intent_id: str) -> bool:
    return intent_id in HUMAN_DECISION_IDS


def is_hardship(intent_id: str) -> bool:
    return intent_id in HARDSHIP_IDS


def expected_attachment(intent_id: str) -> str:
    intent = _BY_ID.get(intent_id)
    return intent.expected_attachment if intent else "none"


def name_for(intent_id: str) -> str:
    if intent_id == OTHER_UNKNOWN:
        return "Other / Unknown"
    intent = _BY_ID.get(intent_id)
    return intent.name if intent else intent_id


def render_for_prompt() -> str:
    """Render the taxonomy as guidance for the intent identifier."""
    lines = []
    for i in INTENTS:
        kind = "PERSON DECIDES" if i.human_decision else "information or routine"
        signals = "; ".join(i.signals)
        lines.append(
            f"- id: {i.id}  ({i.code}, {kind})\n"
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
