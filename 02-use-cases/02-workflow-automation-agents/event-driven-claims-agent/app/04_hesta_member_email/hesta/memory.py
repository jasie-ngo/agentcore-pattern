"""AgentCore Memory session manager with graceful degradation.

Port of ``app/hesta-claimsagent/memory/session.py``, reshaped as a
``session_manager.type`` factory so ``config.yaml`` can declare it:

    session_manager:
      type: ./hesta/memory.py:hesta_session_manager
      params:
        actor_id: hesta-context-manager
        memory_id: ${MEMORY_CLAIMSAGENTMEMORY_ID:-${AGENTCORE_MEMORY_ID:-}}
        top_k: 5
        relevance_score: 0.5

``strands-compose`` supports ``provider: agentcore`` natively, but that path
requires a non-empty ``memory_id`` and cannot fall back when Memory is not
deployed.  This factory keeps the hand-written agent's behaviour: with a
``memory_id`` it returns an ``AgentCoreMemorySessionManager`` bound to the same
``claims/{actorId}/facts`` (SEMANTIC) and ``claims/{actorId}/{sessionId}``
(SUMMARIZATION) namespaces; without one it returns a local
``FileSessionManager`` so the agent still works in local dev and pre-deploy.

One difference from the hand-written agent, called out in the README: there the
actor was keyed per invocation on the member/policy number.  A YAML-declared
session manager is resolved once per session, so ``actor_id`` is static (the
agent name).  Session-scoped recall is unchanged; cross-email recall is scoped
to the agent rather than to the individual member.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strands.session import FileSessionManager
from strands.session.session_manager import SessionManager

from hesta.settings import REGION

log = logging.getLogger(__name__)


def hesta_session_manager(
    *,
    session_id: str,
    actor_id: str,
    memory_id: str = "",
    top_k: int = 5,
    relevance_score: float = 0.5,
    namespace_prefix: str = "claims",
    region: str | None = None,
    storage_dir: str | None = None,
) -> SessionManager:
    """Return an AgentCore Memory session manager, or a local fallback.

    Args:
        session_id: Injected by strands-compose — the runtime session id from the
            ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`` header.
        actor_id: Memory actor. Must be unique per agent.
        memory_id: AgentCore Memory id. Empty ⇒ fall back to ``FileSessionManager``.
        top_k: Records returned per namespace by semantic search.
        relevance_score: Minimum relevance for a retrieved record.
        namespace_prefix: Namespace root, matching the ``agentcore.json`` strategies.
        region: AWS region for Memory. Defaults to ``AWS_REGION``.
        storage_dir: Directory for the local fallback. Defaults to a temp dir.

    Returns:
        An ``AgentCoreMemorySessionManager`` when ``memory_id`` is set, else a
        ``FileSessionManager``.
    """
    # YAML ${VAR:-default} interpolation yields strings, so numeric params arrive
    # as text whenever the default is used.
    top_k = int(top_k)
    relevance_score = float(relevance_score)

    if not memory_id:
        fallback_dir = storage_dir or str(Path(tempfile.gettempdir()) / "hesta-sessions")
        log.warning(
            "actor=<%s> | AgentCore Memory not configured (MEMORY_CLAIMSAGENTMEMORY_ID / "
            "AGENTCORE_MEMORY_ID unset) — falling back to local file sessions at %s",
            actor_id,
            fallback_dir,
        )
        return FileSessionManager(session_id=session_id, storage_dir=fallback_dir)

    try:
        from bedrock_agentcore.memory.integrations.strands.config import (
            AgentCoreMemoryConfig,
            RetrievalConfig,
        )
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )
    except ImportError:
        raise ImportError(
            "AgentCore Memory requires the agentcore-memory extra:\n"
            "  pip install 'strands-compose[agentcore-memory]'"
        ) from None

    # Retrieval config aligned with the agentcore.json memory namespaces:
    #   - claims/{actorId}/facts            (SEMANTIC)      — prior contacts
    #   - claims/{actorId}/{sessionId}      (SUMMARIZATION) — session summaries
    retrieval_config = {
        f"{namespace_prefix}/{actor_id}/facts": RetrievalConfig(
            top_k=top_k, relevance_score=relevance_score
        ),
        f"{namespace_prefix}/{actor_id}/{session_id}": RetrievalConfig(
            top_k=max(top_k - 2, 1), relevance_score=relevance_score
        ),
    }

    log.info("actor=<%s>, memory_id=<%s> | AgentCore Memory session active", actor_id, memory_id)
    return AgentCoreMemorySessionManager(
        AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config=retrieval_config,
        ),
        region or REGION,
    )
