"""MCP Gateway access — Identity-managed Cognito M2M OAuth, direct tool calls.

The pilot reuses the EXISTING Gateway tools (no new Lambdas):
  - lookup_policy         → identity verification (AI-003)
  - create_claim          → write a case record (human-in-the-loop hand-off)
  - request_human_review  → write a review record (human-in-the-loop hand-off)

IMPORTANT — where the Cognito token is fetched
-----------------------------------------------
The M2M access token is fetched by the ``@requires_access_token`` decorator when
``_build_mcp_client()`` is called. That fetch depends on the AgentCore workload-identity
**ContextVar** (``BedrockAgentCoreContext._workload_access_token``), which the runtime
populates on the request/event-loop thread. So the client MUST be built on that thread —
exactly like the original claimsagent, which builds it inside the async entrypoint
(``get_mcp_client()`` is called while constructing the Phase-1 agent).

Building it on a different thread (e.g. inside ``asyncio.to_thread``) loses that context
and the M2M exchange fails with:
    GetResourceOauth2Token ... Error parsing ClientCredentials response

Therefore: ``get_mcp_client()`` is called from the entrypoint, ``start()`` opens the
session, and tool calls reuse the started client via ``call_tool`` (async) — mirroring
claimsagent's Phase-1 (agent starts it) + Phase-3 (direct call_tool) split.
"""

from __future__ import annotations

import json
import logging
import uuid

from bedrock_agentcore.identity.auth import requires_access_token
from config import GATEWAY_CREDENTIAL_PROVIDER, GATEWAY_OAUTH_SCOPES, GATEWAY_URL
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

log = logging.getLogger(__name__)

# Per-invocation record of every Gateway call (name/input/output). The console "tools used"
# panel only ever captures the first call (runtime-side, not controllable from agent code),
# so the orchestrator renders this complete log in its response. Reset per invocation.
TOOL_CALL_LOG: list[dict] = []


def reset_tool_log() -> None:
    global TOOL_CALL_LOG
    TOOL_CALL_LOG = []


def _dump_input(arguments: dict) -> str:
    try:
        return json.dumps(arguments, indent=2, default=str)
    except Exception:  # noqa: BLE001
        return str(arguments)


def _safe_repr(obj, limit: int = 4000) -> str:
    try:
        s = str(obj)
    except Exception:  # noqa: BLE001
        s = repr(obj)
    return s if len(s) <= limit else s[:limit] + " …[truncated]"

# AgentCore Gateway namespaces every tool as "<target>___<tool>". Map bare names
# (used by deterministic calls) to the Gateway's namespaced names.
GATEWAY_TOOL_NAMES = {
    "lookup_policy": "policy-lookup___lookup_policy",
    "create_claim": "create-claim___create_claim",
    "request_human_review": "human-review___request_human_review",
    "send_notification": "notification___send_notification",
    "list_pending_claims": "list-pending-claims___list_pending_claims",
    "resolve_claim": "resolve-claim___resolve_claim",
}

# Why the last build attempt failed (surfaced to the user by AI-003 / HITL).
LAST_ERROR: str | None = None

# bare tool name -> actual Gateway-exposed name (discovered at runtime); None until resolved.
# Tool names are stable across invocations, so this stays cached; the client does NOT.
_resolved_names: dict[str, str] | None = None

def gateway_configured() -> bool:
    return bool(GATEWAY_URL)


@requires_access_token(
    provider_name=GATEWAY_CREDENTIAL_PROVIDER,
    auth_flow="M2M",
    scopes=GATEWAY_OAUTH_SCOPES.replace(",", " ").split(),
)
def _build_mcp_client(*, access_token: str) -> MCPClient:
    """Build the MCPClient with an Identity-managed Cognito M2M token.

    The decorator fetches the token from the AgentCore Identity vault (locally, from
    .env.local under LOCAL_DEV=1). MUST run on the runtime request thread (see module docstring).
    """

    def _transport():
        headers = {"Authorization": f"Bearer {access_token}"}
        return streamablehttp_client(GATEWAY_URL, headers=headers)

    return MCPClient(_transport)


def get_mcp_client() -> MCPClient | None:
    """Build a FRESH MCP client, or None if unavailable.

    NOT cached: each invocation gets a clean client so a start()/stop() from a previous
    invocation cannot collide (avoids "the client session is currently running"). Call this
    from the entrypoint (request/event-loop thread) so the Cognito M2M token fetch has the
    workload-identity context. The caller starts/stops the returned client.
    """
    global LAST_ERROR
    if not GATEWAY_URL:
        LAST_ERROR = "GATEWAY_URL not set (AGENTCORE_GATEWAY_URL / AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL missing)"
        log.warning(LAST_ERROR)
        return None
    try:
        return _build_mcp_client()  # decorator injects the M2M access_token
    except Exception as exc:  # noqa: BLE001
        LAST_ERROR = f"could not build MCP client (Cognito M2M token/identity): {exc!r}"
        log.warning(LAST_ERROR)
        return None


def _loads_json(text: str):
    """json.loads, transparently unwrapping a double-encoded JSON string.

    The Gateway wraps a Lambda's ``json.dumps(item)`` string as a JSON string itself,
    so the tool text can be '"{\\"claim_id\\": ...}"' — one loads gives a str, a second
    gives the object. Returns a dict/list/str (or the original text if not JSON).
    """
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def _parse_result(result) -> dict:
    """Extract a tool's JSON payload from an MCPToolResult.

    MCPToolResult is a TypedDict: {status, toolUseId, content:[{text|json, ...}]}. Access it
    with keys (not attributes). Always returns a dict; on tool error returns {"error": ...}.
    """
    if isinstance(result, dict):
        content = result.get("content") or []
        status = result.get("status")
    else:  # defensive: object-style result
        content = getattr(result, "content", None) or []
        status = getattr(result, "status", None)

    text = None
    for block in content:
        t = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if t is not None:
            text = t
            break
        j = block.get("json") if isinstance(block, dict) else getattr(block, "json", None)
        if isinstance(j, dict):
            return {"error": j} if status == "error" else j

    if text is None:
        return {"error": f"tool returned no content (status={status})"} if status == "error" else {"raw": str(result)}

    payload = _loads_json(text)
    if isinstance(payload, list):
        payload = {"items": payload}
    elif not isinstance(payload, dict):
        payload = {"raw": str(payload)}

    if status == "error" and "error" not in payload:
        payload = {"error": payload.get("raw") or payload.get("message") or payload}
    return payload


def resolve_tool_names(mcp: MCPClient) -> dict[str, str]:
    """Discover the Gateway's actual tool names and map our bare names to them.

    The Gateway namespaces tools as "<target>___<tool>", but rather than trust that
    format we read the live list via ``list_tools_sync`` and match on the last segment.
    Cached after the first successful call. Falls back to the hardcoded map if listing fails.
    """
    global _resolved_names
    if _resolved_names is not None:
        return _resolved_names
    try:
        available = [t.tool_name for t in mcp.list_tools_sync()]
        by_bare = {name.split("___")[-1]: name for name in available}
        resolved = {}
        for bare, default in GATEWAY_TOOL_NAMES.items():
            resolved[bare] = by_bare.get(bare) or (default if default in available else bare)
        _resolved_names = resolved
        log.info("Gateway tools available: %s", available)
        log.info("Resolved tool map: %s", resolved)
    except Exception as exc:  # noqa: BLE001 — fall back to the static map
        log.warning("Could not list Gateway tools (%s); using static tool map.", exc)
        _resolved_names = dict(GATEWAY_TOOL_NAMES)
    return _resolved_names


async def call_tool(mcp: MCPClient, tool_name: str, arguments: dict) -> dict:
    """Call a Gateway tool on an already-started client (async), returning its JSON payload.

    Returns ``{"_gateway_error": "<reason>"}`` on failure instead of raising.
    """
    input_str = _dump_input(arguments)
    if mcp is None:
        err = LAST_ERROR or "Gateway client unavailable"
        TOOL_CALL_LOG.append({"name": tool_name, "input": input_str, "output": err})
        return {"_gateway_error": err}

    resolved = resolve_tool_names(mcp).get(tool_name, GATEWAY_TOOL_NAMES.get(tool_name, tool_name))
    raw = ""
    try:
        result = await mcp.call_tool_async(
            tool_use_id=f"hesta-{uuid.uuid4().hex[:8]}",
            name=resolved,
            arguments=arguments,
        )
        raw = _safe_repr(result)
        payload = _parse_result(result)
    except Exception as exc:  # noqa: BLE001
        log.warning("Gateway call '%s' failed: %s", tool_name, exc)
        payload = {"_gateway_error": f"Gateway call '{tool_name}' failed: {exc!r}"}
        raw = payload["_gateway_error"]

    TOOL_CALL_LOG.append({"name": resolved, "input": input_str, "output": raw})
    return payload
