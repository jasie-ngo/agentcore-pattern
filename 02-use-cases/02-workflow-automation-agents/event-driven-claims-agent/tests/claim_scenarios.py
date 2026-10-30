"""Scripted multi-email threads for the claim lifecycle. Synthetic addresses and ids only.

Each scenario is replayed against the real graph with a fake pipeline (the pipeline's own behaviour is covered by its own tests).
`pipeline` is what the fake pipeline reports for that email: whether it escalates, and what the draft expects next.
`{ref}` in a subject is replaced with the first claim's reference. Only an email after the first may use `{ref}`.

Staff steps: `{"after_email": n, "event": ...}` applies a staff event after email n (1-based). `event` is one of
staff_released, staff_resolved, staff_closed or member_silent. `expects` (optional) is what the released draft asks of the member.
`draft_message_id` (optional) names the outbound draft the step releases, so header threading can be checked.
`refused: True` (optional) means applying the event must be REFUSED (the lifecycle raises LifecycleError) and the claim's
state must be unchanged.

`expect` semantics:
- `final_state`: the final state of the FIRST claim created in the scenario (the one whose reference replaces `{ref}`).
- `claims`: the number of distinct claim records stored at the end, including a parked record for an unmatched reply.
- `refs_in_subjects`: True means every outbound draft subject produced in the scenario starts with `[CLR-`.
  False means no assertion is made about draft subjects. It does NOT mean "must not".
- When an email's `pipeline.expects` and a staff step's `expects` both apply, the STAFF step's value governs the transition.
  The pipeline value is only the draft's stated expectation.
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
        "name": "escalation is sticky: a member_silent event on an escalated claim is refused and the claim stays awaiting_staff",
        "emails": [mail(A, "Hardship", "<m1@x>", escalate=True), mail(A, "Re: [{ref}] Hardship", "<m2@x>")],
        "staff": [{"after_email": 1, "event": "member_silent", "refused": True}],
        "expect": {"final_state": "awaiting_staff", "claims": 1, "refs_in_subjects": True},
    },
    {
        "name": "escalation after a release is sticky: member_silent is refused once the claim is back in awaiting_staff",
        "emails": [
            mail(A, "Question", "<m1@x>"),
            mail(A, "Re: [{ref}] Question", "<m2@x>", escalate=True),
        ],
        "staff": [{"after_email": 1, "event": "staff_released", "expects": "member"},
                  {"after_email": 2, "event": "member_silent", "refused": True}],
        "expect": {"final_state": "awaiting_staff", "claims": 1, "refs_in_subjects": True},
    },
]
