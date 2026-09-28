"""MemoryManager — AgentCore Memory lifecycle management.

Handles session setup, history loading with idempotency filtering, and turn recording.
All methods return safe defaults on failure (never raises).
"""

from __future__ import annotations

import logging
from memory.session import (
    get_memory_session_manager,
    load_thread,
    append_turns,
    safe_memory_id,
    thread_session_id,
)

log = logging.getLogger(__name__)


class MemoryManager:
    """Stateless manager for AgentCore Memory operations.

    All methods return safe defaults when Memory is unavailable or fails,
    enabling graceful degradation.
    """

    @staticmethod
    async def setup_session(member_id: str | None, case_id: str | None) -> dict | None:
        """Setup a Memory session for a verified member + case.

        Returns:
            dict with keys: actor_id, session_id, session_manager, error (if any)
            None if member_id or case_id missing (not an error, just no session)
        """
        if not member_id or not case_id:
            return None

        actor_id = safe_memory_id(member_id)
        session_id = thread_session_id(case_id)

        try:
            session_manager = get_memory_session_manager(session_id, actor_id)
            return {
                "actor_id": actor_id,
                "session_id": session_id,
                "session_manager": session_manager,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("Memory session setup failed (actor=%s session=%s): %s", actor_id, session_id, exc)
            return {
                "actor_id": actor_id,
                "session_id": session_id,
                "session_manager": None,
                "error": f"session setup failed: {exc}",
            }

    @staticmethod
    async def load_history(
        actor_id: str,
        session_id: str,
        idempotency_key: str,
    ) -> list[dict]:
        """Load conversation history from Memory for this actor/session.

        Filters out any entries from this exact invocation (same idempotency_key)
        to prevent retry loops from seeing their own earlier partial writes.

        Returns:
            List of history entries (empty list on failure, never raises)
        """
        try:
            history = load_thread(actor_id, session_id)
            # Strip any entries this exact invocation would own (idempotency)
            prefix = f"{idempotency_key}:"
            filtered = [
                entry for entry in history
                if not str(entry.get("entry_id") or "").startswith(prefix)
            ]
            log.debug("Loaded %d history entries (filtered %d own-retry entries)", len(filtered), len(history) - len(filtered))
            return filtered
        except Exception as exc:  # noqa: BLE001
            log.warning("Memory load failed (actor=%s session=%s): %s", actor_id, session_id, exc)
            return []

    @staticmethod
    async def record_turns(
        actor_id: str,
        session_id: str,
        entries: list[dict],
    ) -> bool:
        """Record conversation turns to Memory.

        Args:
            actor_id: Member ID (from safe_memory_id)
            session_id: Case ID (from thread_session_id)
            entries: List of turn entries (inbound, draft, review, etc.)

        Returns:
            True if recorded successfully, False on failure
        """
        if not entries:
            return False

        try:
            recorded = append_turns(actor_id, session_id, entries)
            log.info("Recorded %d turns to Memory (actor=%s session=%s)", len(entries), actor_id, session_id)
            return bool(recorded)
        except Exception as exc:  # noqa: BLE001
            log.warning("Memory record failed (actor=%s session=%s, %d entries): %s", actor_id, session_id, len(entries), exc)
            return False
