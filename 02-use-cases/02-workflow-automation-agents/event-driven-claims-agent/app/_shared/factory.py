"""Agent factory referenced from every use case's ``config.yaml``.

    agents:
      <name>:
        type: ../_shared/factory.py:agent

``strands-compose`` builds a plain ``strands.Agent`` from each ``agents:`` block.
This factory is a thin shim over that default, and it exists for exactly two
things YAML cannot express on its own:

1. **Structured output contracts.**  ``Agent(structured_output_model=...)`` needs
   a Python class; YAML can only carry a name.  So the config says
   ``agent_kwargs.structured_output: <use_case>.schemas:IntentResult`` and this
   factory imports that class.  Because strands returns structured output
   through ``Agent.as_tool()`` as a ``json`` content block, a coordinator
   receives validated objects rather than prose.

2. **The Identity-managed Gateway MCP client.**  Its Cognito M2M bearer token
   must be fetched on the request thread, after the YAML has already been parsed
   — see the long note in ``_shared/gateway.py``.  ``agent_kwargs.gateway: true``
   attaches a freshly built client as a tool provider, so the agent sees the live
   Gateway tool list.

Everything else — models, prompts, descriptions, tools, session managers,
orchestration — stays in ``config.yaml``.  This file adds no behaviour of its
own and contains no use-case logic.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

# This file is loaded by path (importlib), not as a package member, so the
# app root (the folder holding _shared/ and every use case) has to be importable
# before `_shared.*` or `<use_case>.schemas` resolves.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strands import Agent

from _shared.gateway import build_gateway_client

log = logging.getLogger(__name__)


def _resolve_structured_output(spec: str) -> type:
    """Resolve a ``package.module:ClassName`` spec to the class itself."""
    module_path, sep, class_name = spec.partition(":")
    if not sep or not module_path or not class_name:
        raise ValueError(
            f"agent_kwargs.structured_output: '{spec}' must be 'package.module:ClassName' "
            "(e.g. '<use_case>.schemas:MyResult')"
        )
    module = importlib.import_module(module_path)
    model = getattr(module, class_name, None)
    if model is None:
        available = sorted(
            attr for attr in vars(module) if isinstance(getattr(module, attr), type) and not attr.startswith("_")
        )
        raise ValueError(
            f"agent_kwargs.structured_output: '{class_name}' is not defined in {module_path}.\n"
            f"Available: {', '.join(available)}"
        )
    return model


def agent(
    *,
    structured_output: str | None = None,
    gateway: bool = False,
    tools: list | None = None,
    **kwargs,
) -> Agent:
    """Build one pipeline agent.

    Args:
        structured_output: ``package.module:ClassName`` of a Pydantic class.  The
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
    return build_agent(
        structured_output_model=_resolve_structured_output(structured_output) if structured_output else None,
        gateway=gateway,
        tools=tools,
        **kwargs,
    )


def build_agent(
    *,
    structured_output_model: type | None = None,
    gateway: bool = False,
    tools: list | None = None,
    **kwargs,
) -> Agent:
    """Build an agent from an already-resolved structured-output class.

    Shared by :func:`agent` (class named in YAML) and the agent blueprints in
    ``_shared/agent_blueprints.py`` (class generated from the YAML form).
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
        structured_output_model=structured_output_model,
        load_tools_from_directory=False,
        **kwargs,
    )
