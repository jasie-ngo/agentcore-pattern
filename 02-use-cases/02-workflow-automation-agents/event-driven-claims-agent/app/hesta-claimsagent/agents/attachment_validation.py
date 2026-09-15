"""AI-004 — Attachment Validation Agent.

Pilot scope: attachments arrive only as markers (no file bytes), so this step is
deterministic — it compares what the primary intent EXPECTS against what was
detected, and flags missing/unexpected documents. Real document parsing (type,
completeness, legibility) is post-pilot.
"""

from __future__ import annotations

from intents import taxonomy
from models import AttachmentAssessment


def assess(inbound, intent_result) -> AttachmentAssessment:
    n = inbound.attachment_count
    expected = taxonomy.expected_attachment(intent_result.primary_intent_id)

    if expected == "none":
        if n == 0:
            status = "not_applicable"
            notes = "No attachment expected for this intent, and none detected."
        else:
            # Pilot scope: any marker is treated as present and accepted, even when unexpected.
            status = "present"
            notes = f"{n} attachment(s) detected; not expected for this intent — check relevance."
    else:
        if n == 0:
            status = "missing"
            notes = f"Expected a {expected} for this request, but no attachment was detected — ask the member to provide it."
        else:
            # Pilot scope: a detected marker is presence-of-marker only (no byte-level inspection),
            # but is treated as present and accepted — the Writer must not ask for it again.
            status = "present"
            notes = (
                f"{n} attachment(s) detected; expected a {expected}. Treated as present for this pilot "
                "(marker-only — contents are not inspected)."
            )

    return AttachmentAssessment(
        attachments_present=n,
        expected_document=expected,
        status=status,
        notes=notes,
    )
