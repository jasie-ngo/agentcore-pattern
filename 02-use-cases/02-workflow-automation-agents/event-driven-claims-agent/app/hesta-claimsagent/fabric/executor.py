"""Graph executor — walks a FabricConfig's workflow.

Runs nodes in topological "waves": all nodes whose dependencies are satisfied in a
given wave run concurrently via asyncio.gather, so independent branches execute in
parallel (ADR-0015 decision 1: "an edge-based graph supports parallelism, not just a
linear script"), while everything downstream still sees a fully-populated shared state
once its own wave starts.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Awaitable, Callable

from . import registry
from .schema import FabricConfig, FabricConfigError, WorkflowSpec

OnStep = Callable[[str, dict], "Awaitable[None] | None"]


def _incoming(workflow: WorkflowSpec, node_id: str):
    return [e for e in workflow.edges if e.target == node_id]


def _roots(workflow: WorkflowSpec) -> set[str]:
    return {n.id for n in workflow.nodes if not _incoming(workflow, n.id)}


def _node_active(workflow: WorkflowSpec, node_id: str, state: dict) -> bool:
    incoming = _incoming(workflow, node_id)
    if not incoming:
        return True
    if any(e.router is None for e in incoming):
        return True
    return any(registry.ROUTERS[e.router](state) for e in incoming)


def _adapter_for(node) -> "registry.NodeAdapter":
    table = registry.AGENT_NODES if node.type == "agent" else registry.DETERMINISTIC_NODES
    if node.implementation not in table:
        raise FabricConfigError(f"no registered {node.type} adapter named '{node.implementation}'")
    return table[node.implementation]


async def _call(adapter, state: dict) -> dict:
    result = adapter(state)
    if inspect.isawaitable(result):
        result = await result
    return result or {}


class GraphExecutor:
    """Executes ``config.workflow`` over a shared mutable ``state`` dict."""

    def __init__(self, config: FabricConfig):
        self._config = config

    async def run(self, state: dict, on_step: OnStep | None = None) -> dict:
        workflow = self._config.workflow
        node_by_id = {n.id: n for n in workflow.nodes}

        roots = _roots(workflow)
        if roots != {workflow.start}:
            raise FabricConfigError(
                f"graph must have exactly one root node matching workflow.start="
                f"'{workflow.start}', found {sorted(roots)}"
            )

        done: set[str] = set()
        skipped: set[str] = set()
        remaining: set[str] = set(node_by_id)

        def ready(node_id: str) -> bool:
            return all(e.source in done or e.source in skipped for e in _incoming(workflow, node_id))

        while remaining:
            wave = [nid for nid in remaining if ready(nid)]
            if not wave:
                raise FabricConfigError(f"graph has a cycle or unreachable node(s): {sorted(remaining)}")

            active = [nid for nid in wave if _node_active(workflow, nid, state)]
            skipped.update(nid for nid in wave if nid not in active)
            remaining.difference_update(wave)

            async def _run_one(nid: str) -> None:
                update = await _call(_adapter_for(node_by_id[nid]), state)
                state.update(update)
                done.add(nid)
                if on_step is not None:
                    maybe = on_step(nid, state)
                    if inspect.isawaitable(maybe):
                        await maybe

            await asyncio.gather(*(_run_one(nid) for nid in active))

        return state
