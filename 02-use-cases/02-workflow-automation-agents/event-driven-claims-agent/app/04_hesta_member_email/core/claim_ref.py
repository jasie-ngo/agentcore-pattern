"""Claim references: how an inbound email is tied to one claim.

We mint a reference at first contact and put it in the subject of our reply, so the member's replies carry it back. The mail headers
(In-Reply-To, References) are a second signal that backs it up. This module only decides which claim an email belongs to, and never
guesses: an email that looks like a reply but cannot be matched with confidence goes to a person. Pure Python, no I/O: the lookups
are passed in.

Client-neutral. The prefix is not ``CLM-`` (the create_claim tool's ids) so the two kinds never collide in the claims table.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Callable

PREFIX = "CLR"
_REF = re.compile(r"\bCLR-[0-9A-F]{8}\b", re.IGNORECASE)
_REPLY = re.compile(r"^\s*(re|fw|fwd)\s*:", re.IGNORECASE)
_ADDRESS = re.compile(r"<([^<>]+)>")


def mint_claim_ref(rand: Callable[[], str] = lambda: secrets.token_hex(4)) -> str:
    return f"{PREFIX}-{rand().upper()}"


def refs_in_subject(subject: str) -> list[str]:
    seen: list[str] = []
    for found in _REF.findall(subject or ""):
        ref = found.upper()
        if ref not in seen:
            seen.append(ref)
    return seen


def normalise_address(raw: str) -> str:
    match = _ADDRESS.search(raw or "")
    return (match.group(1) if match else (raw or "")).strip().lower()


@dataclass(frozen=True)
class InboundMail:
    message_id: str
    in_reply_to: str
    references: tuple
    subject: str
    sender: str


@dataclass(frozen=True)
class Resolution:
    kind: str  # "new" | "existing" | "needs_person"
    claim_ref: str | None
    reason: str


def looks_like_reply(mail: InboundMail) -> bool:
    return bool(_REPLY.match(mail.subject or "") or mail.in_reply_to or mail.references)


def resolve(
    mail: InboundMail,
    *,
    sender_of_ref: Callable[[str], str | None],
    ref_of_message_id: Callable[[str], str | None],
) -> Resolution:
    subject_refs = refs_in_subject(mail.subject)
    if len(subject_refs) > 1:
        return Resolution("needs_person", None, "more than one claim reference in the subject")
    header_refs = {
        ref
        for ref in (ref_of_message_id(mid) for mid in (mail.in_reply_to, *mail.references) if mid)
        if ref
    }
    candidates = set(subject_refs) | header_refs
    if len(candidates) > 1:
        return Resolution("needs_person", None, "the subject and the mail headers disagree about the claim")
    if candidates:
        ref = next(iter(candidates))
        owner = sender_of_ref(ref)
        if owner is None:
            return Resolution("needs_person", None, f"reference {ref} matches no claim")
        if normalise_address(owner) != normalise_address(mail.sender):
            return Resolution("needs_person", None, f"the sender does not match the sender of claim {ref}")
        return Resolution("existing", ref, "matched")
    if looks_like_reply(mail):
        return Resolution("needs_person", None, "looks like a reply but carries no usable claim reference")
    return Resolution("new", None, "first contact")
