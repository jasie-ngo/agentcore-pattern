"""AgentCore Memory session manager with graceful degradation.

The session manager attaches to the Strands Agent and automatically records
each conversation turn to AgentCore Memory. The SEMANTIC strategy enables
cross-session recall (e.g., prior claims for repeat claimants), while
SUMMARIZATION compresses session history to prevent context overflow.

If Memory is not deployed or unavailable (local dev, pre-deploy), the agent
continues working without memory — it just won't recall prior interactions.
"""

import logging
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


def record_interaction(actor_id: str, session_id: str, user_text: str, assistant_text: str) -> bool:
    """Explicitly persist this contact as an AgentCore Memory event.

    Needed because AI-002 gets its summary via ``structured_output_async``, which uses a
    temporary message list and never fires the session manager's MessageAddedEvent — so nothing
    is written by the session manager alone. Writing the event here feeds the SEMANTIC (facts)
    and SUMMARIZATION (session) strategies so records actually populate.

    Returns True if an event was written, False otherwise (never raises).
    """
    if not MEMORY_ID:
        return False
    try:
        from bedrock_agentcore.memory import MemoryClient

        client = MemoryClient(region_name=REGION)
        client.create_event(
            memory_id=MEMORY_ID,
            actor_id=actor_id,
            session_id=session_id,
            messages=[
                ((user_text or "")[:8000], "USER"),
                ((assistant_text or "")[:8000], "ASSISTANT"),
            ],
        )
        return True
    except Exception as exc:  # noqa: BLE001 — memory is best-effort; never break processing
        log.warning("Failed to record memory event (actor=%s): %s", actor_id, exc)
        return False


def get_memory_session_manager(session_id: str, actor_id: str) -> Optional[AgentCoreMemorySessionManager]:
    """Create a session manager bound to a specific session and actor.

    Args:
        session_id: Unique session identifier (e.g., claim-{policy_number}-{timestamp}).
        actor_id: The claimant or user who initiated the interaction.

    Returns:
        AgentCoreMemorySessionManager if MEMORY_ID is configured, else None.
    """
    if not MEMORY_ID:
        return None

    # Retrieval config aligned with agentcore.json memory namespaces:
    #   - claims/{actorId}/facts (SEMANTIC) — prior claim history for this claimant
    #   - claims/{actorId}/{sessionId} (SUMMARIZATION) — session summaries
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
