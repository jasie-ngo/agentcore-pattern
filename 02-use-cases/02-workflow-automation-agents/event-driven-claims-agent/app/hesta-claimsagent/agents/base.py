"""Shared agent construction.

Cost-based model routing (reused from the original demo): a cheap/fast model for
classification-style agents, the stronger model for summarisation / writing / review.
Most analysis agents are stateless cached singletons. Agents that benefit from
cross-email recall (e.g. AI-002 Context Manager) are built per-invocation with an
AgentCore Memory ``session_manager`` so prior contacts from the same member are recalled.
"""

from __future__ import annotations

from config import AGENT_MODEL_ID, FAST_MODEL_ID, GUARDRAIL_BLOCK_SENTINEL, GUARDRAIL_ID, GUARDRAIL_VERSION
from strands import Agent
from strands.models.bedrock import BedrockModel


def _build_model(fast: bool, guarded: bool) -> BedrockModel:
    model_id = FAST_MODEL_ID if fast else AGENT_MODEL_ID
    if guarded and GUARDRAIL_ID:
        # Attach the Bedrock Guardrail (no personal advice). Redacts blocked output to the
        # sentinel so the orchestrator can detect an intervention and fall back safely.
        return BedrockModel(
            model_id=model_id,
            guardrail_id=GUARDRAIL_ID,
            guardrail_version=GUARDRAIL_VERSION,
            guardrail_trace="enabled",
            guardrail_redact_output=True,
            guardrail_redact_output_message=GUARDRAIL_BLOCK_SENTINEL,
        )
    return BedrockModel(model_id=model_id)


def build_agent(system_prompt: str, *, fast: bool = False, session_manager=None, guarded: bool = False) -> Agent:
    """Build a Strands agent.

    guarded=True attaches the Bedrock Guardrail (when GUARDRAIL_ID is configured) — used by
    the Writer so it cannot emit personal financial advice.
    """
    kwargs = {"model": _build_model(fast, guarded), "system_prompt": system_prompt}
    if session_manager is not None:
        # Attaching a session manager makes the agent record turns to AgentCore Memory and
        # recall relevant history for this actor/session.
        kwargs["session_manager"] = session_manager
    return Agent(**kwargs)
