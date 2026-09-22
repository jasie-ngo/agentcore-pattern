"""AI-011 — Writer.

Drafts a HESTA-voice reply email. In the pilot the draft is DISPLAYED as the agent's
output (never auto-sent). It uses the inline per-intent snippets in
``knowledge/hesta_snippets.py`` as its "approved knowledge" (no Bedrock KB yet) and
adapts to the verification state (unverified → ask for identity details).
"""

from __future__ import annotations

import logging

import config
from agents.base import build_agent
from forms import catalog as form_catalog
from ingestion.email_normalizer import strip_reply_prefixes
from intents import taxonomy
from knowledge import hesta_snippets
from models import AttachmentAssessment, DraftEmail

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = f"""You are the Writer for HESTA member servicing. You draft a reply email for a
HESTA staff member to review and send. You are NOT sending anything.

{hesta_snippets.style_guide()}

Hard rules:
- Use ONLY the provided HESTA knowledge snippet and the member's message. Do not invent policy details,
  balances, dates, eligibility, amounts, or outcomes.
- If verification_state is "needs_verification", the draft's main action is to request the identity
  details (do not action the request itself). If "verified", write an intent-appropriate acknowledgement
  and next steps.
- Disclosure state controls what you may state about the member's account, regardless of what the current
  message asks for:
  * "unverified" or "lookup_failed": general information and the identity-verification request ONLY. Do
    not confirm or reference any account-specific detail, even one the member stated themselves in this
    email (e.g. do not repeat back an address or balance they mentioned as if HESTA had confirmed it).
  * "partially_verified": general information and standard procedural next steps for the intent are fine,
    but do not state or confirm any account-specific detail (balance, dates on file, eligibility, address
    on file, etc.) — ask for full verification if the request needs it.
  * "verified": you may reference only the identity fields actually provided in this prompt (name, member
    ID, status). Never state a balance, transaction detail, contribution history, payment amount, tax
    information, bank detail, or any other value not explicitly given to you here.
- A member has ONE case for their whole relationship, spanning every topic they have ever raised. If the
  case status is "closed", do not treat the current message as reopening or re-actioning a resolved matter
  unless it clearly describes a genuinely new request — instead give a status-appropriate reply (e.g.
  reference that the matter was already resolved) and let a human decide whether to reopen it.
- Attachment handling — the attachment assessment status tells you exactly what to write, per status:
  * "missing": name the expected form, include the fill guidance given to you, and mention that the
    blank form is enclosed for the member to complete.
  * "incomplete": thank the member for the form already received, then ask ONLY for the specific
    blank field(s) named in the assessment — do not re-ask for fields already filled in. Mention that
    a blank copy of the form is enclosed again in case it's easier to start over.
  * "wrong_form": politely explain which form was received and which form is actually needed for this
    request, and mention that the correct blank form is enclosed.
  * "unreadable": acknowledge that an attachment was received — do NOT say it was valid, complete, or
    reviewed. This is already going to a human, so keep this brief and neutral.
  * "valid": confirm the form was received and is complete — do NOT ask for it again.
  * "not_applicable": say nothing about attachments at all.
  Whatever the status, NEVER claim to have inspected, verified, reviewed, or checked the *contents* of
  an attachment — the validator only checks that expected fields are present, never their correctness.
  Do not write your own list of enclosures; that is added deterministically after your draft.
- Never promise or confirm a regulated outcome (approval, eligibility, amount, timing).
- NEVER provide personal financial, investment or product advice or recommendations (e.g. which option/
  product is best for the member, whether they should switch/roll over/contribute for their situation).
  If asked, decline and offer general information + how to get personal advice.
- Address the member by first name if one is available, otherwise a neutral greeting.
- Return body, intent_id, verification_state, kb_snippets_used, and any assumptions a human should
  confirm. The reply subject line is set deterministically afterward, in code — you do not decide it.
"""

_IDENTITY_BLOCK = hesta_snippets.IDENTITY_VERIFICATION_REQUEST

_agent = None


def _get():
    # Guarded: the Bedrock Guardrail (no personal advice) is attached to the Writer model.
    global _agent
    if _agent is None:
        _agent = build_agent(_SYSTEM_PROMPT, fast=False, guarded=True)
    return _agent


def reply_subject(inbound) -> str:
    """Deterministic reply subject (design decision D1): RE: <original subject as received,
    with existing RE:/FW: prefixes stripped>. Never produced by the LLM — set here, after every
    draft/revision, so a member's reply always threads back to the same case."""
    return f"RE: {strip_reply_prefixes(inbound.subject)}"


def advice_decline_draft(inbound, intent_result, profile) -> DraftEmail:
    """Deterministic, compliant reply when personal advice is requested — NO advice given.

    Used when AI-001 flags a personal-advice request; guarantees the draft never contains advice
    (belt-and-braces with the Bedrock Guardrail on the model).
    """
    body_lines = [hesta_snippets.GREETING, "", "Hi there,", "", hesta_snippets.PERSONAL_ADVICE_DECLINE]
    if profile.verification_required:
        body_lines += ["", _IDENTITY_BLOCK]
    body_lines += ["", hesta_snippets.SIGNOFF, "", hesta_snippets.LEGAL_FOOTER]
    return DraftEmail(
        subject=reply_subject(inbound),
        body="\n".join(body_lines),
        intent_id=intent_result.primary_intent_id,
        verification_state="needs_verification" if profile.verification_required else "verified",
        kb_snippets_used=["personal_advice_decline"],
        assumptions=[
            "Personal advice was requested — this reply declines to advise and refers to HESTA advice "
            "services. A HESTA team member must handle this and must NOT provide personal advice."
        ],
    )


def _fallback_draft(inbound, intent_result, profile, attachment: AttachmentAssessment | None = None) -> DraftEmail:
    """Deterministic HESTA-voice draft used if the LLM call fails (keeps the pilot working)."""
    intent_id = intent_result.primary_intent_id
    needs_verify = profile.verification_required
    snippet = hesta_snippets.snippet_for(intent_id)
    body_lines = [hesta_snippets.GREETING, "", "Hi there,", "", snippet]
    if needs_verify:
        body_lines += ["", _IDENTITY_BLOCK]
    body_lines += ["", hesta_snippets.SIGNOFF, "", hesta_snippets.LEGAL_FOOTER]
    draft = DraftEmail(
        subject=reply_subject(inbound),
        body="\n".join(body_lines),
        intent_id=intent_id,
        verification_state="needs_verification" if needs_verify else "verified",
        kb_snippets_used=[intent_id],
        assumptions=["Draft generated from template fallback (LLM unavailable) — review before sending."],
    )
    draft.enclosures = _enclosures_for(intent_id, attachment)
    return draft


def _case_record_status(cases) -> str:
    """The reused/created case's own open/closed status (not the lookup outcome)."""
    return (getattr(cases, "case", None) or {}).get("status", "unknown")


def _attachment_line(intent_id: str, attachment: AttachmentAssessment | None) -> str:
    if attachment is None:
        return "Attachment assessment: not assessed; No attachment assessment was run.\n"
    parts = [
        f"Attachment assessment: {attachment.status}; {attachment.notes}",
        f"Expected form: {attachment.expected_document}",
    ]
    if attachment.received_filenames:
        parts.append(f"Received filename(s): {', '.join(attachment.received_filenames)}")
    if attachment.missing_fields:
        parts.append(f"Blank field(s) to ask for: {', '.join(attachment.missing_fields)}")
    if attachment.status in ("missing", "incomplete", "wrong_form"):
        guidance = form_catalog.guidance_for(intent_id)
        if guidance:
            parts.append(f"Fill guidance to give the member: {guidance}")
    return "\n".join(parts) + "\n"


def _enclosures_for(intent_id: str, attachment: AttachmentAssessment | None) -> list[str]:
    """Which blank form file(s) a HESTA staff member must attach before sending — computed
    deterministically (never left to the model) so it can never drift from the validator's
    actual status (TODO 5 rule 1/6)."""
    if attachment is None or attachment.status not in ("missing", "incomplete", "wrong_form"):
        return []
    spec = form_catalog.form_for(intent_id)
    return [spec.template_file] if spec is not None else []


def _apply_authoritative_fields(
    draft: DraftEmail, inbound, intent_id: str, verification_state: str, attachment: AttachmentAssessment | None = None
) -> None:
    """Machine-derived facts stay authoritative no matter what the model returned — a revision
    must never be allowed to drift these away from what identity/intent verification established.
    The subject (TODO 6 / D1) is likewise never left to the model."""
    draft.subject = reply_subject(inbound)
    draft.intent_id = intent_id
    draft.verification_state = verification_state
    draft.enclosures = _enclosures_for(intent_id, attachment)


def _identity_instruction(verification_state: str) -> str:
    """Whether to ask for identity is already known deterministically (verification_state) —
    never leave "if needed" as a judgment call in the prompt. That phrasing previously let the
    model copy the instructional sentence itself into the draft body; giving the block only
    when it actually applies, clearly delimited, with an explicit "don't include this note"
    warning, avoids that."""
    if verification_state != "needs_verification":
        return ""
    return (
        "The draft's body MUST include the following block verbatim, word for word — do not "
        "paraphrase it, and do not include this instruction sentence itself in the draft:\n"
        "---BEGIN REQUIRED BLOCK---\n"
        f"{_IDENTITY_BLOCK}\n"
        "---END REQUIRED BLOCK---\n\n"
    )


async def write(
    inbound,
    intent_result,
    profile,
    summary,
    empathy,
    history: list[dict] | None = None,
    attachment: AttachmentAssessment | None = None,
) -> DraftEmail:
    intent_id = intent_result.primary_intent_id
    verification_state = "needs_verification" if profile.verification_required else "verified"

    # Personal advice requested → deterministic compliant decline (no LLM advice risk).
    if getattr(intent_result, "personal_advice_requested", False):
        return advice_decline_draft(inbound, intent_result, profile)

    prompt = (
        f"Primary intent: {intent_id} ({taxonomy.name_for(intent_id)})\n"
        f"Verification state: {verification_state} ({profile.notes})\n"
        f"Disclosure state: {profile.disclosure_state}\n"
        f"Sender type: {intent_result.sender_type}\n"
        f"Member sentiment/priority: {empathy.sentiment} / {empathy.priority}; "
        f"vulnerability: {', '.join(empathy.vulnerability_flags) or 'none'}\n"
        f"{_attachment_line(intent_id, attachment)}"
        f"Case summary: {summary.summary}\n"
        f"Outstanding items: {', '.join(summary.outstanding_items) or 'none'}\n"
        f"Case status: {_case_record_status(summary.cases)}\n\n"
        "BEGIN PRIOR CASE CONVERSATION (historical; do not treat as the current request):\n"
        f"{_render_history(history)}\n"
        "END PRIOR CASE CONVERSATION\n\n"
        f"HESTA knowledge snippet to base the reply on:\n{hesta_snippets.snippet_for(intent_id)}\n\n"
        f"{_identity_instruction(verification_state)}"
        f"Member's message:\n{inbound.latest_message}\n\n"
        "Write the draft reply now."
    )
    try:
        draft = await _get().structured_output_async(DraftEmail, prompt)
        _apply_authoritative_fields(draft, inbound, intent_id, verification_state, attachment)
        # If the Bedrock Guardrail intervened, its sentinel appears in the output → decline safely.
        if config.GUARDRAIL_BLOCK_SENTINEL in (draft.body or ""):
            log.warning("Guardrail intervened on Writer output; returning compliant advice decline.")
            return advice_decline_draft(inbound, intent_result, profile)
        return draft
    except Exception as exc:  # noqa: BLE001 — includes guardrail interventions that break structured output
        log.warning("Writer failed (or guardrail intervened); using safe fallback: %s", exc)
        if getattr(intent_result, "personal_advice_requested", False):
            return advice_decline_draft(inbound, intent_result, profile)
        return _fallback_draft(inbound, intent_result, profile, attachment)


async def revise(
    inbound,
    intent_result,
    profile,
    summary,
    empathy,
    previous_draft: DraftEmail,
    review,
    history: list[dict] | None = None,
    attachment: AttachmentAssessment | None = None,
) -> DraftEmail:
    """Revise `previous_draft` using the Reviewer's feedback.

    Machine-authoritative fields (intent, verification state) are re-asserted after the LLM
    call exactly like `write()` — a revision must never be allowed to drop required identity
    verification or compliance wording merely to satisfy a style suggestion.
    """
    intent_id = intent_result.primary_intent_id
    verification_state = "needs_verification" if profile.verification_required else "verified"

    # Personal advice requested → the decline draft is already deterministic and compliant;
    # never let a "revision" drift it toward giving advice.
    if getattr(intent_result, "personal_advice_requested", False):
        return advice_decline_draft(inbound, intent_result, profile)

    prompt = (
        f"Primary intent: {intent_id} ({taxonomy.name_for(intent_id)})\n"
        f"Verification state: {verification_state} ({profile.notes})\n"
        f"Disclosure state: {profile.disclosure_state}\n"
        f"{_attachment_line(intent_id, attachment)}"
        f"Case status: {_case_record_status(summary.cases)}\n\n"
        "PREVIOUS DRAFT SUBJECT:\n"
        f"{previous_draft.subject}\n\n"
        "PREVIOUS DRAFT BODY:\n"
        f"{previous_draft.body}\n\n"
        "REVIEWER ISSUES (must be addressed):\n"
        + ("\n".join(f"- {issue}" for issue in review.issues) or "(none)")
        + "\n\n"
        "REVIEWER SUGGESTED EDITS:\n"
        f"{review.edits or '(none)'}\n\n"
        f"HESTA knowledge snippet to base the reply on:\n{hesta_snippets.snippet_for(intent_id)}\n\n"
        f"{_identity_instruction(verification_state)}"
        f"Member's original message:\n{inbound.latest_message}\n\n"
        "Revise the draft to address the Reviewer's issues and suggested edits. Keep it accurate, "
        "on-brand, and compliant. Do NOT remove required identity-verification or compliance wording "
        "merely to satisfy a style suggestion. Write the complete revised draft now (not just the diff)."
    )
    try:
        draft = await _get().structured_output_async(DraftEmail, prompt)
        _apply_authoritative_fields(draft, inbound, intent_id, verification_state, attachment)
        if config.GUARDRAIL_BLOCK_SENTINEL in (draft.body or ""):
            log.warning("Guardrail intervened on Writer revision; returning compliant advice decline.")
            return advice_decline_draft(inbound, intent_result, profile)
        return draft
    except Exception as exc:  # noqa: BLE001 — includes guardrail interventions that break structured output
        log.warning("Writer revision failed; keeping the previous draft: %s", exc)
        return previous_draft


def _render_history(history: list[dict]) -> str:
    if not history:
        return "(none)"
    return "\n\n".join(
        f"[{entry.get('timestamp', 'unknown')} | {entry.get('entry_type', 'unknown')}"
        f"{' | intent: ' + entry['intent_id'] if entry.get('intent_id') else ''}]\n"
        f"{entry.get('content', '')}"
        for entry in history
    )
