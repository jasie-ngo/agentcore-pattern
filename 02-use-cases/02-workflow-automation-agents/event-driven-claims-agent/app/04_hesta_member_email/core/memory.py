"""AgentCore Memory for the YAML pipeline, keyed on a VERIFIED member only.

Why this is not a session manager
---------------------------------
``AgentCoreMemorySessionManager`` fixes its actor when it is built and uses it for everything: it persists the whole
conversation and recalls prior contacts under that one actor.  A YAML-declared session manager is built once per
session, so the actor could only be a constant (the agent name).  Every member's contacts were pooled under one actor,
and recall drew on all of them.  The deployed pilot avoids that by keying on the member number, but it takes the number
from the email text and opens memory BEFORE the sender's identity is checked.

So memory here is two explicit, gated calls, made after identity verification:

    recall(member_number, sender_email, query)      -> the member's earlier contacts
    remember(member_number, sender_email, ...)      -> record this contact

Both do nothing unless ``classify_verification`` has verified THIS member number for THIS sender address (it registers the
pair, see ``register_verified``).  A different sender quoting the same member number is not in the registry, so it can
neither read the member's memory nor write into it.  The actor is the member number itself, in the same format the
deployed pilot uses, so the ``claims/{actorId}/facts`` and ``claims/{actorId}/{sessionId}`` namespaces are unchanged.

What this does not do: "verified" is only as strong as the sender-address match that decides it (an address can be
spoofed), and in this pipeline the lookup fields reach ``classify_verification`` through the identity agent.  Both are
properties of the verification policy, not of this module.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections import OrderedDict
from threading import Lock

from core.settings import REGION

log = logging.getLogger(__name__)

NAMESPACE_PREFIX = "claims"
_MEMBER_NUMBER = re.compile(r"[0-9A-Za-z]{4,24}")  # strict on purpose: no sanitising, so no two inputs share an actor
_MAX_TEXT = 8000
_MAX_VERIFIED = 1000

_verified: "OrderedDict[tuple[str, str], None]" = OrderedDict()
_lock = Lock()


def _memory_id() -> str:
    return os.getenv("MEMORY_CLAIMSAGENTMEMORY_ID") or os.getenv("AGENTCORE_MEMORY_ID", "")


def _top_k() -> int:
    try:
        return max(int(os.getenv("MEMORY_RETRIEVAL_TOP_K", "5")), 1)
    except ValueError:
        return 5


def _relevance() -> float:
    try:
        return float(os.getenv("MEMORY_RETRIEVAL_RELEVANCE", "0.5"))
    except ValueError:
        return 0.5


def _client():
    from bedrock_agentcore.memory import MemoryClient

    return MemoryClient(region_name=REGION)


def memory_actor(member_number: str | None) -> str | None:
    """The Memory actor for a member number, or None when it is not a plain member number."""
    number = (member_number or "").strip()
    return number if _MEMBER_NUMBER.fullmatch(number) else None


def _pair(member_number: str | None, sender_email: str | None) -> tuple[str, str] | None:
    actor = memory_actor(member_number)
    email = (sender_email or "").strip().lower()
    return (actor, email) if actor and email else None


def register_verified(member_number: str | None, sender_email: str | None) -> None:
    """Record that this member number was verified for this sender address. Called by ``classify_verification``."""
    pair = _pair(member_number, sender_email)
    if pair is None:
        return
    with _lock:
        _verified[pair] = None
        _verified.move_to_end(pair)
        while len(_verified) > _MAX_VERIFIED:
            _verified.popitem(last=False)


def is_verified(member_number: str | None, sender_email: str | None) -> bool:
    pair = _pair(member_number, sender_email)
    with _lock:
        return pair is not None and pair in _verified


def forget_all() -> None:
    """Empty the registry. For tests."""
    with _lock:
        _verified.clear()


_NOT_VERIFIED = "identity is not verified for this member number and sender address, so memory is not used"


def recall(member_number: str | None, sender_email: str | None, query: str | None, *, client=None) -> dict:
    """The member's earlier contacts. ``allowed`` is False, with nothing recalled, unless identity was verified."""
    if not is_verified(member_number, sender_email):
        return {"allowed": False, "memories": [], "reason": _NOT_VERIFIED}
    memory_id = _memory_id()
    if not memory_id:
        return {"allowed": True, "memories": [], "reason": "AgentCore Memory is not configured"}
    actor = memory_actor(member_number)
    try:
        records = (client or _client()).retrieve_memories(
            memory_id=memory_id,
            namespace_path=f"{NAMESPACE_PREFIX}/{actor}/facts",
            query=(query or "")[:_MAX_TEXT] or "member contact",
            top_k=_top_k(),
        )
    except Exception as exc:  # noqa: BLE001 - memory is best-effort; it must never break processing
        log.warning("actor=<%s> | memory recall failed: %s", actor, type(exc).__name__)
        return {"allowed": True, "memories": [], "reason": f"memory recall failed ({type(exc).__name__})"}
    floor = _relevance()
    texts = []
    for record in records or []:
        if isinstance(record, dict) and record.get("score", 0.0) >= floor:
            content = record.get("content", {})
            text = content.get("text", "").strip() if isinstance(content, dict) else ""
            if text:
                texts.append(text)
    return {"allowed": True, "memories": texts, "reason": f"{len(texts)} earlier contact(s) recalled"}


def remember(member_number: str | None, sender_email: str | None, member_message: str | None,
             summary: str | None, *, client=None) -> dict:
    """Record this contact under the member's actor. ``recorded`` is False unless identity was verified."""
    if not is_verified(member_number, sender_email):
        return {"recorded": False, "reason": _NOT_VERIFIED}
    memory_id = _memory_id()
    if not memory_id:
        return {"recorded": False, "reason": "AgentCore Memory is not configured"}
    actor = memory_actor(member_number)
    try:
        (client or _client()).create_event(
            memory_id=memory_id,
            actor_id=actor,
            session_id=f"session-{actor}-{uuid.uuid4().hex}",
            messages=[((member_message or "")[:_MAX_TEXT], "USER"), ((summary or "")[:_MAX_TEXT], "ASSISTANT")],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("actor=<%s> | memory write failed: %s", actor, type(exc).__name__)
        return {"recorded": False, "reason": f"memory write failed ({type(exc).__name__})"}
    return {"recorded": True, "reason": "contact recorded"}
