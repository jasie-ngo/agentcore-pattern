"""AI-004 — Attachment Validation Agent.

Fully deterministic (no LLM): opens each real attachment the Trigger Lambda parsed
off the inbound ``.eml``, matches it against the form the primary intent expects
(``forms/catalog.py``), and checks its fields for presence (never semantic
correctness — see ``forms/parser.py``).
"""

from __future__ import annotations

from forms import catalog, parser
from models import AttachmentAssessment


def _previously_valid(case_history: list[dict] | None) -> bool:
    """True if any earlier turn in this case ever had a *validated valid* form on file.

    Case-wide, not scoped to the current intent (existing behaviour) — but the bar is
    raised (TODO 4 rule 5): a previously incomplete or wrong form no longer counts as
    already on file, only a previously ``valid`` one does.
    """
    return any(entry.get("attachment_status") == "valid" for entry in (case_history or []))


def assess(inbound, intent_result, case_history: list[dict] | None = None) -> AttachmentAssessment:
    intent_id = intent_result.primary_intent_id
    spec = catalog.form_for(intent_id)
    received_filenames = [a.filename for a in inbound.attachments]

    if spec is None:
        return AttachmentAssessment(
            attachments_present=inbound.attachment_count,
            expected_document="none",
            status="not_applicable",
            notes="No form is expected for this intent.",
            received_filenames=received_filenames,
        )

    expected = spec.name

    if not inbound.attachments:
        if _previously_valid(case_history):
            return AttachmentAssessment(
                attachments_present=0,
                expected_document=expected,
                status="valid",
                notes=(
                    f"No attachment in this message, but a valid {expected} was provided earlier "
                    "in this case — treat it as already on file. Do not ask for it again."
                ),
                form_id=spec.form_id,
                form_name=expected,
            )
        return AttachmentAssessment(
            attachments_present=0,
            expected_document=expected,
            status="missing",
            notes=(
                f"Expected a {expected} for this request, but no attachment was detected — ask "
                "the member to provide it."
            ),
            form_name=expected,
        )

    # Parse every readable attachment — a member may attach several documents, so collect
    # ALL candidates and prefer whichever one matches the expected form, rather than
    # whichever happens to parse first. Without this, "wrong form first, correct filled
    # form second" would wrongly report wrong_form (and the result would depend on MIME
    # part order, which is not something a member controls).
    candidates = []
    unreadable_reasons: list[str] = []
    for attachment in inbound.attachments:
        if attachment.text is None:
            unreadable_reasons.append(attachment.skipped_reason or "not decodable as text")
            continue
        candidate = parser.parse_form(attachment.text)
        if candidate is None:
            unreadable_reasons.append("not recognisable as a HESTA form (no HESTA-FORM-ID header)")
            continue
        candidates.append(candidate)

    parsed = next((c for c in candidates if c.form_id == spec.form_id), None)
    if parsed is None and candidates:
        parsed = candidates[0]

    if parsed is None:
        reason = "; ".join(dict.fromkeys(unreadable_reasons)) or "no attachment could be read"
        return AttachmentAssessment(
            attachments_present=len(inbound.attachments),
            expected_document=expected,
            status="unreadable",
            notes=f"Expected a {expected}, but {reason} — route to human review.",
            form_name=expected,
            received_filenames=received_filenames,
        )

    if parsed.form_id != spec.form_id:
        received_spec = catalog.spec_by_form_id(parsed.form_id)
        received_name = received_spec.name if received_spec else (parsed.form_id or "an unrecognised form")
        return AttachmentAssessment(
            attachments_present=len(inbound.attachments),
            expected_document=expected,
            status="wrong_form",
            notes=(
                f"Expected the {expected} (form id {spec.form_id}), but received {received_name} "
                f"(form id {parsed.form_id or 'unknown'}) instead."
            ),
            form_id=parsed.form_id,
            form_name=received_name,
            received_filenames=received_filenames,
        )

    missing_fields = [f.label for f in spec.fields if parser.is_blank(parsed.fields.get(f.label))]
    if missing_fields:
        return AttachmentAssessment(
            attachments_present=len(inbound.attachments),
            expected_document=expected,
            status="incomplete",
            notes=(
                f"Received the {expected}, but the following field(s) are blank: "
                f"{', '.join(missing_fields)}. Field values are checked for presence only — their "
                "content has not been semantically verified."
            ),
            form_id=spec.form_id,
            form_name=expected,
            missing_fields=missing_fields,
            received_filenames=received_filenames,
        )

    return AttachmentAssessment(
        attachments_present=len(inbound.attachments),
        expected_document=expected,
        status="valid",
        notes=(
            f"Received a complete {expected} — every expected field is filled in. Field values are "
            "checked for presence only; their content has not been semantically verified."
        ),
        form_id=spec.form_id,
        form_name=expected,
        received_filenames=received_filenames,
    )
