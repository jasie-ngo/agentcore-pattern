"""Per-intent blank form catalog (TODO 1).

Each of the 8 taxonomy intents gets exactly one blank plain-text form template,
packaged with the Runtime container under ``forms/templates/``. This module is
the registry: it maps an intent id to its ``FormSpec`` (form id, name, field
list, template file, member-facing fill guidance) and exposes the lookups the
rest of the pipeline (taxonomy, validator, writer) needs.

Validated at import time: every taxonomy intent resolves to a template file
that exists and declares the right ``HESTA-FORM-ID`` and field labels. A
mismatch here is a packaging error, not a runtime condition, so it raises
loudly rather than surfacing as a mysterious validation failure later.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from intents.taxonomy import INTENTS

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class FormField:
    key: str
    label: str


@dataclass(frozen=True)
class FormSpec:
    form_id: str
    name: str
    intent_id: str
    template_file: str
    fields: tuple[FormField, ...]
    guidance: str


_COMMON_FIELDS = (
    FormField("member_name", "Member name"),
    FormField("member_email", "Member email"),
    FormField("member_number", "Member number"),
)

# (intent_id, form_id, form_name, template_file, 4th field key, 4th field label, member-facing guidance)
_SPECS_RAW: list[tuple[str, str, str, str, str, str, str]] = [
    (
        "death_benefit_nomination", "BDBN-NOM-V1", "Binding Death Benefit Nomination Form",
        "bdbn_nomination_v1.txt", "nomination_details", "Nomination details",
        "Please complete every field, including exactly who you are nominating and what share "
        "of your benefit each beneficiary should receive, then attach the completed form to "
        "your reply.",
    ),
    (
        "withdrawal_benefit_payment", "BP-WD-V1", "Benefit Payment / Withdrawal Form",
        "bp_withdrawal_v1.txt", "withdrawal_details", "Withdrawal details",
        "Please complete every field, including the amount you wish to withdraw and the reason "
        "for the withdrawal, then attach the completed form to your reply.",
    ),
    (
        "change_of_details", "COD-DET-V1", "Change of Personal Details Form",
        "cod_change_of_details_v1.txt", "detail_changes", "Details to update",
        "Please complete every field, including exactly which details (mobile, email, address) "
        "should change and their new values, then attach the completed form to your reply.",
    ),
    (
        "departing_australia_payment", "DASP-CLM-V1",
        "Departing Australia Superannuation Payment Claim Form",
        "dasp_claim_v1.txt", "departure_details", "Departure details",
        "Please complete every field, including your departure date and current country of "
        "residence, then attach the completed form to your reply.",
    ),
    (
        "financial_hardship", "FH-HDS-V1", "Financial Hardship Form",
        "fh_hardship_v1.txt", "hardship_reason", "Reason for hardship",
        "Please complete every field, including a clear description of your hardship "
        "circumstances, then attach the completed form and any supporting evidence to your "
        "reply.",
    ),
    (
        "family_law_split", "FLS-SPLIT-V1", "Family Law Superannuation Split Form",
        "fls_split_v1.txt", "court_order_details", "Court order details",
        "Please complete every field, including the relevant court order or agreement details, "
        "then attach the completed form to your reply.",
    ),
    (
        "notice_of_intent_tax_deduction", "NOI-TAX-V1",
        "Notice of Intent to Claim a Tax Deduction Form",
        "noi_tax_deduction_v1.txt", "deduction_details", "Deduction details",
        "Please complete every field, including the financial year and the amount you intend "
        "to claim as a deduction, then attach the completed form to your reply.",
    ),
    (
        "rollover_transfer_combine", "RTC-ROLLOVER-V1", "Rollover / Transfer / Combine Accounts Form",
        "rtc_rollover_v1.txt", "transfer_details", "Transfer details",
        "Please complete every field, including the fund you are transferring from or to, then "
        "attach the completed form to your reply.",
    ),
]

_SPECS: dict[str, FormSpec] = {}
for _intent_id, _form_id, _name, _template_file, _fkey, _flabel, _guidance in _SPECS_RAW:
    _fields = _COMMON_FIELDS + (FormField(_fkey, _flabel),)
    _SPECS[_intent_id] = FormSpec(
        form_id=_form_id,
        name=_name,
        intent_id=_intent_id,
        template_file=_template_file,
        fields=_fields,
        guidance=_guidance,
    )

_BY_FORM_ID: dict[str, FormSpec] = {spec.form_id: spec for spec in _SPECS.values()}


def form_for(intent_id: str) -> FormSpec | None:
    return _SPECS.get(intent_id)


def spec_by_form_id(form_id: str) -> FormSpec | None:
    return _BY_FORM_ID.get(form_id)


def blank_template(intent_id: str) -> str:
    spec = form_for(intent_id)
    if spec is None:
        raise KeyError(f"No form defined for intent {intent_id!r}")
    return (_TEMPLATES_DIR / spec.template_file).read_text(encoding="utf-8")


def guidance_for(intent_id: str) -> str:
    spec = form_for(intent_id)
    return spec.guidance if spec else ""


def _validate_catalog() -> None:
    """Import-time packaging check — raise loudly on any drift between the catalog, the
    taxonomy, and the template files actually shipped in the container."""
    seen_form_ids: set[str] = set()
    for intent in INTENTS:
        spec = _SPECS.get(intent.id)
        if spec is None:
            raise RuntimeError(f"forms.catalog: no FormSpec registered for intent {intent.id!r}")
        if spec.form_id in seen_form_ids:
            raise RuntimeError(f"forms.catalog: duplicate form_id {spec.form_id!r}")
        seen_form_ids.add(spec.form_id)

        path = _TEMPLATES_DIR / spec.template_file
        if not path.exists():
            raise RuntimeError(f"forms.catalog: template file missing for intent {intent.id!r}: {path}")
        text = path.read_text(encoding="utf-8")
        if f"HESTA-FORM-ID: {spec.form_id}" not in text:
            raise RuntimeError(
                f"forms.catalog: {spec.template_file} does not declare HESTA-FORM-ID {spec.form_id!r}"
            )
        for field in spec.fields:
            if field.label not in text:
                raise RuntimeError(
                    f"forms.catalog: {spec.template_file} is missing field label {field.label!r}"
                )


_validate_catalog()
