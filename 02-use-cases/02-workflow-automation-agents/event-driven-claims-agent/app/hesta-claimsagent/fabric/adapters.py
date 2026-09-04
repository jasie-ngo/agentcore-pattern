"""Node adapters — thin async wrappers giving each existing agent/deterministic function
a uniform ``async def(state: dict) -> dict`` signature so it can run as a fabric graph
node (ADR-0015 decisions 1 and 3). No agent prompt/logic changes here — glue only.
"""

from __future__ import annotations

from . import registry


@registry.agent_node("intent_identifier")
async def _intent_identifier(state: dict) -> dict:
    from agents import intent_identifier

    result = await intent_identifier.identify(state["inbound"])
    return {"intent_result": result}


@registry.agent_node("context_manager")
async def _context_manager(state: dict) -> dict:
    from agents import context_manager
    from memory.session import record_interaction

    summary = await context_manager.summarize(state["inbound"], session_manager=state.get("memory_session"))
    memory_recorded = None
    if state.get("memory_session") is not None:
        memory_recorded = record_interaction(
            state["actor_id"], state["session_id"], state["inbound"].latest_message, summary.summary
        )
    return {"summary": summary, "memory_recorded": memory_recorded}


@registry.agent_node("empathy")
async def _empathy(state: dict) -> dict:
    from agents import empathy as empathy_agent

    result = await empathy_agent.assess(state["inbound"])
    return {"empathy": result}


@registry.agent_node("writer")
async def _writer(state: dict) -> dict:
    from agents import writer as writer_agent

    draft = await writer_agent.write(
        state["inbound"],
        state["intent_result"],
        state["profile"],
        state["summary"],
        state["empathy"],
        status_ctx=state.get("status_ctx"),
    )
    return {"draft": draft}


@registry.agent_node("reviewer_editor")
async def _reviewer_editor(state: dict) -> dict:
    from agents import reviewer_editor

    result = await reviewer_editor.review(state["draft"], state["intent_result"], state["profile"])
    return {"review": result}


@registry.deterministic_node("identity_profiling")
async def _identity_profiling(state: dict) -> dict:
    from agents import identity_profiling

    profile = await identity_profiling.profile(state["mcp"], state["inbound"], state["intent_result"])
    return {"profile": profile}


@registry.deterministic_node("attachment_validation")
def _attachment_validation(state: dict) -> dict:
    from agents import attachment_validation

    return {"attachments": attachment_validation.assess(state["inbound"], state["intent_result"])}


@registry.deterministic_node("case_status_lookup")
async def _case_status_lookup(state: dict) -> dict:
    from agents import case_status

    result = await case_status.lookup_pending(state["mcp"], state["inbound"])
    return {"status_ctx": result}


@registry.deterministic_node("routing_decision")
def _routing_decision(state: dict) -> dict:
    from routing import decide

    return {"decision": decide(state["intent_result"], state["profile"], state["empathy"])}


@registry.deterministic_node("hitl_record")
async def _hitl_record(state: dict) -> dict:
    from config import ENABLE_HITL_RECORD

    if not ENABLE_HITL_RECORD:
        return {
            "hitl_message": (
                "_HITL record disabled (ENABLE_HITL_RECORD=false). Escalation reasons: "
                + "; ".join(state["decision"].reasons)
                + "_\n\n"
            )
        }
    from hitl import write_hitl_record

    message = await write_hitl_record(
        state["mcp"], state["inbound"], state["intent_result"], state["profile"], state["decision"], state["draft"]
    )
    return {"hitl_message": message}
