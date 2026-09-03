"""Runtime binding of fabric config names to Python callables (ADR-0015 decision 1).

``bind()`` is called once at cold start (main.py) with the loaded FabricConfig. Agent
modules call ``spec_for()`` to resolve their own per-agent model/guardrail overrides,
falling back to the given defaults when no fabric config is bound (or the config
doesn't declare that agent) so existing behaviour is unchanged.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from .schema import AgentSpec, FabricConfig

NodeAdapter = Callable[[dict], "Awaitable[dict] | dict"]
RouterFn = Callable[[dict], bool]

DETERMINISTIC_NODES: dict[str, NodeAdapter] = {}
AGENT_NODES: dict[str, NodeAdapter] = {}
ROUTERS: dict[str, RouterFn] = {}

_active: FabricConfig | None = None


def bind(config: FabricConfig) -> None:
    """Install ``config`` as the active fabric config for this process."""
    global _active
    _active = config


def reset() -> None:
    """Clear the active config (used by tests for isolation)."""
    global _active
    _active = None


def spec_for(name: str, *, default_fast: bool = False, default_guarded: bool = False) -> AgentSpec:
    """Resolve the AgentSpec for agent ``name``.

    Falls back to defaults (matching pre-fabric hardcoded behaviour) when no fabric
    config is bound, or the bound config doesn't declare this agent.
    """
    if _active is not None and name in _active.agents:
        return _active.agents[name]
    return AgentSpec(name=name, implementation=name, fast=default_fast, guarded=default_guarded)


def deterministic_node(name: str):
    def _register(fn: NodeAdapter) -> NodeAdapter:
        DETERMINISTIC_NODES[name] = fn
        return fn

    return _register


def agent_node(name: str):
    def _register(fn: NodeAdapter) -> NodeAdapter:
        AGENT_NODES[name] = fn
        return fn

    return _register


def router(name: str):
    def _register(fn: RouterFn) -> RouterFn:
        ROUTERS[name] = fn
        return fn

    return _register
