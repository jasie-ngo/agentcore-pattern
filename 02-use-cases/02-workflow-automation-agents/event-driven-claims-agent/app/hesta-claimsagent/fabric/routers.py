"""Router predicates gate optional graph edges (ADR-0015 decisions 1 and 3)."""

from __future__ import annotations

from . import registry


@registry.router("is_status_query")
def _is_status_query(state: dict) -> bool:
    from agents import case_status

    return case_status.is_status_query(state["inbound"], state.get("summary"), state["intent_result"])


@registry.router("escalate_to_human")
def _escalate_to_human(state: dict) -> bool:
    return bool(state["decision"].escalate_to_human)
