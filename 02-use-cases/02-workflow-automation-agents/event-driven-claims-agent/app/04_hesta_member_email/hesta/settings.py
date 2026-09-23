"""Centralized configuration — ALL env var reads live here.

Port of ``app/hesta-claimsagent/config.py``.  The same environment variables are
read, with the same names and defaults, so a deployment configured for the
hand-written agent works unchanged against this YAML-driven one.

The CDK L3 construct auto-generates names like
``AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL`` and ``MEMORY_CLAIMSAGENTMEMORY_ID``;
both the auto-generated and the explicit names are read.

Values that the YAML config can express directly (model ids, guardrail id,
memory id, retrieval tuning, thresholds) are declared in ``config.yaml`` with
``${VAR:-default}`` interpolation instead — see the README.  What is left here
is only what Python needs at runtime: the Gateway connection and the
thresholds used by the deterministic tools.
"""

from __future__ import annotations

import os

# ─── AWS Region ─────────────────────────────────────────────────────────────
REGION = os.getenv("AWS_REGION", "us-west-2")

# ─── Gateway ────────────────────────────────────────────────────────────────
# The L3 construct sets AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL automatically.
# The explicit name passed by infra-construct is read too, for backward compat.
GATEWAY_URL = os.getenv(
    "AGENTCORE_GATEWAY_URL",
    os.getenv("AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL", ""),
)
GATEWAY_OAUTH_SCOPES = os.getenv("AGENTCORE_GATEWAY_OAUTH_SCOPES", "agentcore/invoke")

# Identity credential provider — registered via `agentcore add credential`
# during deploy.  The @requires_access_token decorator uses this name to fetch
# tokens from the AgentCore Identity token vault (Secrets Manager-backed).
GATEWAY_CREDENTIAL_PROVIDER = os.getenv(
    "AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER", "cognito-gateway-m2m"
)

# Seconds to wait for the Gateway MCP handshake before giving up.
GATEWAY_STARTUP_TIMEOUT = int(os.getenv("AGENTCORE_GATEWAY_STARTUP_TIMEOUT", "60"))

# ─── Routing ────────────────────────────────────────────────────────────────
# Minimum confidence (0-100) for a primary intent to be treated as confident.
# Below this, or an "other_unknown" intent, the case escalates to human review.
INTENT_CONFIDENCE_THRESHOLD = int(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "70"))

# When True, escalated cases write a record to DynamoDB via the MCP Gateway
# (reusing create_claim + request_human_review) — the human hand-off.  No new
# AWS resources: it reuses the existing Claims/Reviews tables and Gateway tools.
ENABLE_HITL_RECORD = os.getenv("ENABLE_HITL_RECORD", "true").lower() in ("1", "true", "yes")

# ─── Guardrail (no personal financial advice) ────────────────────────────────
# The guardrail itself is attached to the Writer's model in config.yaml
# (models.guarded.params).  The sentinel is needed here so the Reviewer's
# prompt and the routing tool can recognise an intervention.
GUARDRAIL_BLOCK_SENTINEL = "[GUARDRAIL_BLOCKED_ADVICE]"
