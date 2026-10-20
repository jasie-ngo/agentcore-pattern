"""Generic runtime configuration shared by every use case.

Only the plumbing env vars live here — AWS region and the AgentCore Gateway
connection. Use-case-specific values (thresholds, sentinels, feature flags)
belong in that use case's own ``settings.py``; values the YAML config can
express directly (model ids, guardrail id, memory id, retrieval tuning) are
declared in each ``config.yaml`` with ``${VAR:-default}`` interpolation.

The CDK L3 construct auto-generates names like
``AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL``; both the auto-generated and the
explicit names are read.
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
