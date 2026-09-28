"""AgentCore Memory: case-conversation store + Strands session manager.

The session manager attaches to the Strands Agent and automatically records
each conversation turn to AgentCore Memory. The SEMANTIC strategy enables
cross-session recall (e.g., prior claims for repeat claimants), while
SUMMARIZATION compresses session history to prevent context overflow.

A case's own conversation (inbound emails, generated drafts, review outcomes) is stored
separately, as explicit Memory events in a session keyed by the case id (``thread_session_id``,
``append_turns``, ``load_thread``) — see design decisions D6/D7/D8 in
docs/V2_ENHANCEMENT_TODO.md. This is independent of, and simpler than, the Strands session
manager's own automatic recording, which AI-002's ``structured_output_async`` call never
triggers anyway (it uses a temporary message list, not the agent's normal message loop).

If Memory is not deployed or unavailable (local dev, pre-deploy), everything here degrades to
a no-op / empty result — it never raises, so the agent keeps working without recall. The caller
is responsible for surfacing that degradation to the user (implementation rule 4) rather than
silently presenting a follow-up as a first contact.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from config import MEMORY_ID, MEMORY_RETRIEVAL_RELEVANCE, MEMORY_RETRIEVAL_TOP_K, REGION

log = logging.getLogger(__name__)

# entry_type (case history) -> AgentCore Memory message role.
_ROLE_BY_ENTRY_TYPE = {
    "inbound_member_email": "USER",
    "generated_draft": "ASSISTANT",
    "review_outcome": "OTHER",
}

# Case-history fields carried as event metadata (beyond entry_id/entry_type/intent_id, which
# every entry gets) — only meaningful on the inbound entry.
_INBOUND_METADATA_KEYS = ("attachment_status", "form_id")


def safe_memory_id(value: str | None, fallback: str = "anonymous") -> str:
    """Sanitize an identifier for AgentCore Memory actorId/sessionId.

    Memory rejects '@', '.', and other characters (e.g. raw emails), so map any input to
    [a-zA-Z0-9_-] and guarantee an alphanumeric first character.
    """
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", (value or "").strip())
    safe = re.sub(r"-{2,}", "-", safe).strip("-_")
    if not safe or not safe[0].isalnum():
        safe = f"{fallback}-{safe}".strip("-_") or fallback
    return safe[:200]


def thread_session_id(case_id: str) -> str:
    """Stable AgentCore Memory session id for a case's whole conversation (D8) — the SAME
    session is reused for every email on this case, for the life of the case. Distinct from the
    Runtime session, which stays one per inbound email (D6)."""
    return safe_memory_id(case_id, fallback="case")


def _metadata_for(entry: dict) -> dict:
    metadata: dict = {}
    for key in ("entry_id", "entry_type", "intent_id"):
        value = entry.get(key)
        if value:
            metadata[key] = {"stringValue": str(value)}
    if entry.get("entry_type") == "inbound_member_email":
        for key in _INBOUND_METADATA_KEYS:
            value = entry.get(key)
            if value:
                metadata[key] = {"stringValue": str(value)}
        missing_fields = entry.get("missing_fields")
        if missing_fields:
            metadata["missing_fields"] = {"stringValue": ", ".join(missing_fields)}
    return metadata


def _known_entry_ids(client, actor_id: str, session_id: str) -> set:
    """Entry ids already recorded in this session — used for idempotency on retry (the
    installed MemoryClient.create_event does not accept a client token, so re-processing the
    same idempotency key is de-duplicated by listing first rather than a server-side token)."""
    try:
        events = client.list_events(
            memory_id=MEMORY_ID,
            actor_id=actor_id,
            session_id=session_id,
            max_results=200,
            include_payload=False,
        )
    except Exception as exc:  # noqa: BLE001 — fail open to "nothing known"; a duplicate write is harmless
        log.warning("Could not list memory events for idempotency check (session=%s): %s", session_id, exc)
        return set()
    known = set()
    for event in events:
        entry_id = (event.get("metadata") or {}).get("entry_id", {}).get("stringValue")
        if entry_id:
            known.add(entry_id)
    return known


def append_turns(actor_id: str, session_id: str, entries: list[dict]) -> bool:
    """Append case-history entries (inbound email / generated draft / review outcome) to this
    case's AgentCore Memory session, in order, as one ``create_event`` per entry.

    Ordering is NOT left to ``eventTimestamp`` alone: botocore serializes the timestamp it sends
    to the service rounded to millisecond precision (and the service itself may round further,
    e.g. to the second), so events issued in the same tight loop can still tie even when spaced
    apart in-process. Each entry therefore also carries a ``seq`` metadata value — its position
    in THIS call — and ``load_thread`` sorts by ``(timestamp, seq)``, which stays correct no
    matter how coarsely the timestamp itself gets rounded. The millisecond spacing on
    ``event_timestamp`` below is kept only as a secondary aid for anything that reads the raw
    Memory event timestamp directly.

    Idempotent on retry: entries whose ``entry_id`` already exists in the session are skipped.
    Returns True if Memory is configured and every entry was written or already present; False
    if Memory is unavailable or a write failed (never raises).
    """
    if not MEMORY_ID or not entries:
        return False
    try:
        from bedrock_agentcore.memory import MemoryClient

        client = MemoryClient(region_name=REGION)
        known_ids = _known_entry_ids(client, actor_id, session_id)

        base_time = datetime.now(timezone.utc)
        for index, entry in enumerate(entries):
            entry_id = entry.get("entry_id")
            if entry_id and entry_id in known_ids:
                continue
            role = _ROLE_BY_ENTRY_TYPE.get(entry.get("entry_type"), "OTHER")
            content = (entry.get("content") or "")[:8000]
            metadata = _metadata_for(entry)
            metadata["seq"] = {"stringValue": str(index)}
            client.create_event(
                memory_id=MEMORY_ID,
                actor_id=actor_id,
                session_id=session_id,
                messages=[(content, role)],
                event_timestamp=base_time + timedelta(milliseconds=index),
                metadata=metadata,
            )
        return True
    except Exception as exc:  # noqa: BLE001 — memory is best-effort; never break processing
        log.warning("Failed to append memory turns (actor=%s session=%s): %s", actor_id, session_id, exc)
        return False


def _iso(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _entry_from_event(event: dict) -> dict:
    metadata = event.get("metadata") or {}
    text = ""
    for item in event.get("payload") or []:
        conv = item.get("conversational")
        if conv:
            text = conv.get("content", {}).get("text", "")
            break

    def _meta(key: str) -> str | None:
        return metadata.get(key, {}).get("stringValue")

    entry = {
        "entry_id": _meta("entry_id") or event.get("eventId"),
        "entry_type": _meta("entry_type") or "",
        "content": text,
        "timestamp": _iso(event.get("eventTimestamp")),
    }
    if _meta("intent_id"):
        entry["intent_id"] = _meta("intent_id")
    if _meta("attachment_status"):
        entry["attachment_status"] = _meta("attachment_status")
    if _meta("form_id"):
        entry["form_id"] = _meta("form_id")
    if _meta("missing_fields"):
        entry["missing_fields"] = _meta("missing_fields").split(", ")
    return entry


_MIN_TIMESTAMP = datetime.min.replace(tzinfo=timezone.utc)


def _event_sort_key(event: dict):
    """(eventTimestamp, seq) — seq (written by append_turns) breaks ties whenever the
    timestamp alone can't, which is routine: it gets rounded to millisecond precision (or
    coarser) well before it reaches the service. Events written before this field existed have
    no seq and sort first among same-timestamp events, which is harmless (best-effort ordering
    only, never a correctness issue for those older rows)."""
    ts = event.get("eventTimestamp") or _MIN_TIMESTAMP
    seq_raw = (event.get("metadata") or {}).get("seq", {}).get("stringValue")
    try:
        seq = int(seq_raw)
    except (TypeError, ValueError):
        seq = -1
    return (ts, seq)


def load_thread(actor_id: str, session_id: str, max_turns: int = 30) -> list[dict]:
    """Load this case's conversation from AgentCore Memory, oldest-first, capped at
    ``max_turns``, mapped to the same dict shape the old DynamoDB ``conversation_history``
    entries had (entry_id, entry_type, content, timestamp, intent_id, …) so the Context
    Manager and Writer prompt rendering stays unchanged.

    Sorted by ``(eventTimestamp, seq)`` — see ``append_turns`` for why ``seq`` matters: the
    timestamp alone is not reliably fine-grained enough to order several events from the same
    call.

    Returns [] without raising only when Memory isn't configured (``MEMORY_ID`` unset) — a
    genuinely empty session is indistinguishable from that at this layer. A configured Memory
    that fails to load RAISES, deliberately: implementation rule 4 requires the caller to
    surface that distinctly ("prior conversation could not be loaded"), never silently present
    a follow-up as a first contact by swallowing the failure into the same empty list.
    """
    if not MEMORY_ID:
        return []
    from bedrock_agentcore.memory import MemoryClient

    client = MemoryClient(region_name=REGION)
    events = client.list_events(
        memory_id=MEMORY_ID,
        actor_id=actor_id,
        session_id=session_id,
        max_results=max_turns,
        include_payload=True,
    )
    events = sorted(events, key=_event_sort_key)
    turns = [_entry_from_event(event) for event in events]
    return turns[-max_turns:]


def get_memory_session_manager(session_id: str, actor_id: str) -> Optional[AgentCoreMemorySessionManager]:
    """Create a session manager bound to a specific session and actor.

    Args:
        session_id: Unique session identifier — the case's thread_session_id (D8).
        actor_id: The member who initiated the interaction.

    Returns:
        AgentCoreMemorySessionManager if MEMORY_ID is configured, else None.
    """
    if not MEMORY_ID:
        return None

    # Retrieval config aligned with agentcore.json memory namespaces:
    #   - claims/{actorId}/facts (SEMANTIC) — prior claim history for this claimant
    #   - claims/{actorId}/{sessionId} (SUMMARIZATION) — now a per-case summary, since
    #     sessionId is stable for the life of the case (D8) rather than one per email.
    retrieval_config = {
        f"claims/{actor_id}/facts": RetrievalConfig(
            top_k=MEMORY_RETRIEVAL_TOP_K, relevance_score=MEMORY_RETRIEVAL_RELEVANCE
        ),
        f"claims/{actor_id}/{session_id}": RetrievalConfig(
            top_k=max(MEMORY_RETRIEVAL_TOP_K - 2, 1), relevance_score=MEMORY_RETRIEVAL_RELEVANCE
        ),
    }

    return AgentCoreMemorySessionManager(
        AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config=retrieval_config,
        ),
        REGION,
    )
