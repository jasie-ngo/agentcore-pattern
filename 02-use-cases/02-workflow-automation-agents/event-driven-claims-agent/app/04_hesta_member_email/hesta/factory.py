"""Agent factory referenced from ``config.yaml`` via ``agents.<name>.type``.

``strands-compose`` builds a plain ``strands.Agent`` from each ``agents:`` block.
This factory is a thin shim over that default, and it exists for exactly two
things YAML cannot express on its own:

1. **Structured output contracts.**  ``Agent(structured_output_model=...)`` needs
   a Python class; YAML can only carry a name.  So the config says
   ``agent_kwargs.structured_output: IntentResult`` and this factory resolves the
   name against ``hesta/schemas.py``.  Every specialist keeps the validated
   contract it had in the hand-written agent, and because strands returns
   structured output through ``Agent.as_tool()`` as a ``json`` content block, the
   coordinator receives validated objects rather than prose.

2. **The Identity-managed Gateway MCP client.**  Its Cognito M2M bearer token
   must be fetched on the request thread, after the YAML has already been parsed
   — see the long note in ``hesta/gateway.py``.  ``agent_kwargs.gateway: true``
   attaches a freshly built client as a tool provider, so the agent sees the live
   Gateway tool list.

Everything else — models, prompts, descriptions, tools, session managers,
orchestration — stays in ``config.yaml``.  This file adds no behaviour of its own.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# This file is loaded by path (importlib), not as a package member, so the
# example root has to be importable before `hesta.*` resolves.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strands import Agent

from hesta import schemas
from hesta.gateway import build_gateway_client

log = logging.getLogger(__name__)


def _resolve_structured_output(name: str) -> type:
    """Resolve a ``hesta/schemas.py`` class name to the class itself."""
    model = getattr(schemas, name, None)
    if model is None:
        available = sorted(
            attr
            for attr in vars(schemas)
            if isinstance(getattr(schemas, attr), type) and not attr.startswith("_")
        )
        raise ValueError(
            f"agent_kwargs.structured_output: '{name}' is not defined in hesta/schemas.py.\n"
            f"Available: {', '.join(available)}"
        )
    return model


def hesta_agent(
    *,
    structured_output: str | None = None,
    gateway: bool = False,
    tools: list | None = None,
    **kwargs,
) -> Agent:
    """Build one HESTA pipeline agent.

    Args:
        structured_output: Name of a Pydantic class in ``hesta/schemas.py``.  The
            agent returns a validated instance of it instead of free text.
        gateway: Attach the AgentCore Gateway MCP client as a tool provider.
            When the Gateway is not configured or the M2M token exchange fails,
            the agent is built without it (the prompts tell the agent to report
            that the lookup was unavailable rather than invent a result).
        tools: Tools resolved by strands-compose from the agent's ``tools:`` list.
        **kwargs: Everything strands-compose passes through from the YAML —
            ``name``, ``agent_id``, ``model``, ``system_prompt``, ``description``,
            ``hooks``, ``plugins``, ``conversation_manager``, ``session_manager``,
            plus any remaining ``agent_kwargs``.

    Returns:
        A configured ``strands.Agent``.
    """
    agent_tools = list(tools or [])

    if gateway:
        client = build_gateway_client()
        if client is not None:
            agent_tools.append(client)
            log.info("agent=<%s> | Gateway MCP client attached", kwargs.get("name"))
        else:
            log.warning(
                "agent=<%s> | Gateway MCP client unavailable, continuing without it",
                kwargs.get("name"),
            )

    return Agent(
        tools=agent_tools,
        structured_output_model=(
            _resolve_structured_output(structured_output) if structured_output else None
        ),
        load_tools_from_directory=False,
        **kwargs,
    )
