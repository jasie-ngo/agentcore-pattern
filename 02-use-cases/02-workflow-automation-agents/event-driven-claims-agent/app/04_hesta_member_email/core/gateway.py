"""MCP Gateway access — Identity-managed Cognito M2M OAuth.

Ported from the original hand-written pilot, with one change: instead of
calling Gateway tools imperatively from an orchestrator, the started ``MCPClient``
is handed to the agent as a **tool provider**.  The agents therefore see the real
Gateway tool list and call it themselves; strands owns the session lifecycle
(``start()`` on first use, ``stop()`` when the last consuming agent is torn down).

The pilot reuses the EXISTING Gateway tools (no new Lambdas):
  - lookup_policy         → identity verification (AI-003)
  - list_pending_claims   → existing-case lookup for status enquiries
  - create_claim          → write a case record (human-in-the-loop hand-off)
  - request_human_review  → write a review record (human-in-the-loop hand-off)

IMPORTANT — where the Cognito token is fetched
-----------------------------------------------
The M2M access token is fetched by the ``@requires_access_token`` decorator when
``_build_mcp_client()`` is called.  That fetch depends on the AgentCore
workload-identity **ContextVar** (``BedrockAgentCoreContext._workload_access_token``),
which the runtime populates on the request/event-loop thread.  So the client MUST
be built on that thread.

This is exactly why the Gateway is wired through an agent factory
(``agents.<name>.type`` in ``config.yaml``) rather than a ``mcp_clients:`` block:

* ``mcp_clients:`` header values are interpolated when the YAML is parsed, which
  ``create_app()`` does at import time — before any request exists, so there is
  no workload identity to exchange and no way to refresh an expiring token.
* Agent factories run inside ``load(app_config, session_id=...)``, which
  ``strands-compose-agentcore`` calls from the ``/invocations`` entrypoint — the
  request thread, with the context populated.

Building it on a different thread (e.g. inside ``asyncio.to_thread``) loses that
context and the M2M exchange fails with:
    GetResourceOauth2Token ... Error parsing ClientCredentials response
"""

from __future__ import annotations

import logging

from bedrock_agentcore.identity.auth import requires_access_token
from strands.tools.mcp import MCPClient

# strands-compose's own transport factory, rather than calling the mcp package
# directly: it builds the streamable-HTTP transport the same way a
# `mcp_clients:` block would, and it tracks the mcp SDK's renames for us.
from strands_compose.mcp.transports import streamable_http_transport

from core.settings import (
    GATEWAY_CREDENTIAL_PROVIDER,
    GATEWAY_OAUTH_SCOPES,
    GATEWAY_STARTUP_TIMEOUT,
    GATEWAY_URL,
)

log = logging.getLogger(__name__)

# AgentCore Gateway namespaces every tool as "<target>___<tool>".  A client's prompts name the bare tool and tell the agent
# to match on the suffix, so the core keeps no map of tool names: they belong to the client (and to its rule pack's vocabulary).

# Why the last build attempt failed — surfaced in logs and, for the agents, as an
# absent tool list (they are prompted to say so rather than invent a result).
LAST_ERROR: str | None = None


def gateway_configured() -> bool:
    """True when a Gateway URL is configured."""
    return bool(GATEWAY_URL)


@requires_access_token(
    provider_name=GATEWAY_CREDENTIAL_PROVIDER,
    auth_flow="M2M",
    scopes=GATEWAY_OAUTH_SCOPES.replace(",", " ").split(),
)
def _build_mcp_client(*, access_token: str) -> MCPClient:
    """Build the MCPClient with an Identity-managed Cognito M2M token.

    The decorator fetches the token from the AgentCore Identity vault (locally,
    from ``.env.local`` under ``LOCAL_DEV=1``).  MUST run on the runtime request
    thread — see the module docstring.
    """

    transport = streamable_http_transport(
        GATEWAY_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return MCPClient(
        transport,
        startup_timeout=GATEWAY_STARTUP_TIMEOUT,
        # A Gateway outage must not stop the pipeline: strands logs the failed
        # connection and the agent simply gets no Gateway tools, mirroring the
        # hand-written agent's "degrade gracefully, surface the reason" behaviour.
        continue_on_error=True,
    )


def build_gateway_client() -> MCPClient | None:
    """Build a FRESH Gateway MCP client, or ``None`` if unavailable.

    NOT cached: each session gets a clean client so a ``start()``/``stop()`` from
    a previous session cannot collide ("the client session is currently running").
    Called from the agent factory during per-session agent construction, which
    runs on the request/event-loop thread so the Cognito M2M token fetch has the
    workload-identity context.

    strands starts the client when it is registered as a tool provider on an
    Agent, and stops it when the last consuming agent is torn down — so nothing
    here starts or stops it.
    """
    global LAST_ERROR
    if not GATEWAY_URL:
        LAST_ERROR = (
            "GATEWAY_URL not set "
            "(AGENTCORE_GATEWAY_URL / AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL missing)"
        )
        log.warning("%s — agents will run without Gateway tools", LAST_ERROR)
        return None
    try:
        return _build_mcp_client()  # decorator injects the M2M access_token
    except Exception as exc:  # noqa: BLE001 — degrade gracefully; surface the reason
        LAST_ERROR = f"could not build MCP client (Cognito M2M token/identity): {exc!r}"
        log.warning("%s — agents will run without Gateway tools", LAST_ERROR)
        return None
