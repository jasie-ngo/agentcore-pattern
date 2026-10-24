"""Generic agent factory: what ``strands-compose`` cannot express in YAML, with no client in it.

``strands-compose`` builds a plain ``strands.Agent`` from each ``agents:`` block.  This factory is a thin shim over that
default, and exists for exactly three things:

1. **Structured output contracts.**  ``Agent(structured_output_model=...)`` needs a Python class; YAML can only carry a
   name.  The client passes in its own schemas module and the name is resolved against it.
2. **The Identity-managed Gateway MCP client** (``agent_kwargs.gateway: true``), built on the request thread.
3. **A verified Cedar rule pack** (``agent_kwargs.cedar_pack``): the pack is verified (hashes, approval record, effective
   dates) and an in-process ``CedarAuthorization`` handler checks every tool call, with the agent's own name as the
   principal.  A pack that cannot be verified raises, so the session does not start.  Off unless the value is set.

Everything else (models, prompts, tools, orchestration) stays in the client's ``config.yaml``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType

from strands import Agent

from core.gateway import build_gateway_client

log = logging.getLogger(__name__)


def _resolve_structured_output(schemas: ModuleType, name: str) -> type:
    """Resolve a class name to the class, in the client's schemas module."""
    model = getattr(schemas, name, None)
    if model is None:
        available = sorted(
            attr for attr in vars(schemas) if isinstance(getattr(schemas, attr), type) and not attr.startswith("_")
        )
        raise ValueError(
            f"agent_kwargs.structured_output: '{name}' is not defined in {schemas.__name__}.\n"
            f"Available: {', '.join(available)}"
        )
    return model


def _cedar_interventions(pack_dir: str, agent_name: str | None) -> list:
    """Verify the rule pack and return a one-handler list, or raise.

    There is deliberately no fallback: if the pack is missing, unapproved, outside its effective dates or has been
    altered, the agent is not built and the session does not start.
    """
    if not agent_name:
        raise ValueError("agent_kwargs.cedar_pack needs the agent's name to use as the Cedar principal")
    try:
        from core import cedar_pack  # noqa: PLC0415 - optional: needs the 'cedar' extra

        handler = cedar_pack.load_cedar_authorization(Path(pack_dir), principal={"type": "Agent", "id": agent_name})
    except ImportError as exc:
        raise RuntimeError(
            f"agent '{agent_name}' asks for a Cedar rule pack, but its dependencies are missing ({exc}). "
            "Install the 'cedar' extra (strands-agents[cedar] and PyYAML), or unset cedar_pack."
        ) from exc
    log.info("agent=<%s>, pack=<%s> | Cedar rule pack verified, handler attached", agent_name, pack_dir)
    return [handler]


def build_agent(
    *,
    schemas: ModuleType,
    structured_output: str | None = None,
    gateway: bool = False,
    cedar_pack: str | None = None,
    tools: list | None = None,
    **kwargs,
) -> Agent:
    """Build one pipeline agent. See the module docstring; ``schemas`` is the client's structured-output module."""
    agent_tools = list(tools or [])

    if gateway:
        client = build_gateway_client()
        if client is not None:
            agent_tools.append(client)
            log.info("agent=<%s> | Gateway MCP client attached", kwargs.get("name"))
        else:
            log.warning("agent=<%s> | Gateway MCP client unavailable, continuing without it", kwargs.get("name"))

    if cedar_pack:
        # Keep any interventions the YAML already passes; the Cedar handler is added to them.
        kwargs["interventions"] = [
            *(kwargs.get("interventions") or []),
            *_cedar_interventions(cedar_pack, kwargs.get("name")),
        ]

    return Agent(
        tools=agent_tools,
        structured_output_model=_resolve_structured_output(schemas, structured_output) if structured_output else None,
        load_tools_from_directory=False,
        **kwargs,
    )
