"""Deterministic sensitive-disclosure scan (TODO 5, rule 7).

A pure regex backstop run on the FINAL draft (after the Writer/Reviewer revision loop) to
catch obvious account-detail leaks the LLM layers might still produce: specific dollar
amounts, BSB numbers, bank account numbers, and tax file numbers. This is an
application-layer check — the Bedrock Guardrail's denied topic is the model-level
backstop, and neither is the authorization decision itself (that lives in
``MemberProfile.disclosure_state``, enforced by application logic).

Deliberately keyword-anchored rather than bare digit-run matching, so it does not
false-positive on static boilerplate that already ships in every draft — e.g. the HESTA
legal footer's ABN numbers ("66 006 818 695") are 11 digits in groups of 2-3-3-3 and carry
no "BSB"/"account"/"TFN" label nearby, so they never match.
"""

from __future__ import annotations

import re

_MONEY_RE = re.compile(r"\$\s?\d")
_BSB_RE = re.compile(r"\bBSB\b[^\d]{0,20}\d{3}[- ]?\d{3}\b", re.IGNORECASE)
_BANK_ACCOUNT_RE = re.compile(r"\baccount\s+(?:number|no\.?)\b[^\d]{0,20}\d{4,}", re.IGNORECASE)
_TFN_RE = re.compile(r"\b(?:TFN|tax\s+file\s+number)\b[^\d]{0,20}\d[\d\s-]{7,11}\d\b", re.IGNORECASE)


def scan(subject: str, body: str) -> list[str]:
    """Return human-readable finding labels, or an empty list if nothing matched.

    Findings are labels only (e.g. "possible tax file number (TFN)") — never the matched
    text itself, so a flagged draft doesn't propagate the sensitive value it was flagged
    for into escalation reasons or logs.
    """
    text = f"{subject}\n{body}"
    findings = []
    if _MONEY_RE.search(text):
        findings.append("possible specific dollar amount")
    if _BSB_RE.search(text):
        findings.append("possible BSB number")
    if _BANK_ACCOUNT_RE.search(text):
        findings.append("possible bank account number")
    if _TFN_RE.search(text):
        findings.append("possible tax file number (TFN)")
    return findings
