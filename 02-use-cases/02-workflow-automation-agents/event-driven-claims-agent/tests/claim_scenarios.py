"""Scripted multi-email threads for the claim lifecycle. Synthetic addresses and ids only.

Each scenario is replayed against the real graph with a fake pipeline (the pipeline's own behaviour is covered by its own tests).
`pipeline` is what the fake pipeline reports for that email: whether it escalates, and what the draft expects next.
`{ref}` in a subject is replaced with the first claim's reference.
"""

A = "customer.a@example.com"
B = "customer.b@example.com"


def mail(sender, subject, mid, *, in_reply_to="", references=(), escalate=False, expects="member"):
    return {
        "from": sender, "subject": subject, "message_id": mid, "in_reply_to": in_reply_to, "references": list(references),
        "pipeline": {"escalate": escalate, "expects": expects},
    }


SCENARIOS = [
    {
        "name": "first contact creates one claim awaiting staff",
        "emails": [mail(A, "Question about my claim", "<m1@x>")],
        "staff": [],
        "expect": {"final_state": "awaiting_staff", "claims": 1, "refs_in_subjects": True},
    },
    {
        "name": "release, member reply, release with nothing expected resolves the claim",
        "emails": [
            mail(A, "Question about my claim", "<m1@x>"),
            mail(A, "Re: [{ref}] Question about my claim", "<m2@x>", expects="nothing"),
        ],
        "staff": [{"after_email": 1, "event": "staff_released", "expects": "member"},
                  {"after_email": 2, "event": "staff_released", "expects": "nothing"}],
        "expect": {"final_state": "resolved", "claims": 1, "refs_in_subjects": True},
    },
    {
        "name": "a reply with Re: but no reference and no headers goes to a person, not a new claim",
        "emails": [mail(A, "Question", "<m1@x>"), mail(A, "Re: Question", "<m2@x>")],
        "staff": [{"after_email": 1, "event": "staff_released", "expects": "member"}],
        "expect": {"final_state": "awaiting_staff", "claims": 2, "refs_in_subjects": False},
    },
    {
        "name": "a reference quoted by another sender never attaches",
        "emails": [mail(A, "Question", "<m1@x>"), mail(B, "Re: [{ref}] Question", "<m2@x>")],
        "staff": [],
        "expect": {"final_state": "awaiting_staff", "claims": 2, "refs_in_subjects": False},
    },
    {
        "name": "headers alone carry the thread when the subject lost the reference",
        "emails": [mail(A, "Question", "<m1@x>"), mail(A, "Re: Question", "<m2@x>", in_reply_to="<d1@x>")],
        "staff": [{"after_email": 1, "event": "staff_released", "expects": "member", "draft_message_id": "<d1@x>"}],
        "expect": {"final_state": "awaiting_staff", "claims": 1, "refs_in_subjects": True},
    },
    {
        "name": "escalation is sticky: a member email cannot move a claim out of awaiting_staff",
        "emails": [mail(A, "Hardship", "<m1@x>", escalate=True), mail(A, "Re: [{ref}] Hardship", "<m2@x>")],
        "staff": [],
        "expect": {"final_state": "awaiting_staff", "claims": 1, "refs_in_subjects": True},
    },
]
