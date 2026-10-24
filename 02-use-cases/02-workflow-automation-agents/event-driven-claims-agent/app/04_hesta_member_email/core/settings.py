"""Generic runtime settings: ALL env var reads the client-neutral core needs live here.

The CDK L3 construct auto-generates names like ``AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL``; both the auto-generated and the
explicit names are read.  Values that belong to one client (routing thresholds, the guardrail sentinel) are in that client's
own settings module, not here.
"""

from __future__ import annotations

import os

# ─── AWS Region ─────────────────────────────────────────────────────────────
REGION = os.getenv("AWS_REGION", "us-west-2")

# ─── Gateway ────────────────────────────────────────────────────────────────
GATEWAY_URL = os.getenv(
    "AGENTCORE_GATEWAY_URL",
    os.getenv("AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL", ""),
)
GATEWAY_OAUTH_SCOPES = os.getenv("AGENTCORE_GATEWAY_OAUTH_SCOPES", "agentcore/invoke")

# Identity credential provider, registered via `agentcore add credential` during deploy.
GATEWAY_CREDENTIAL_PROVIDER = os.getenv("AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER", "cognito-gateway-m2m")

# Seconds to wait for the Gateway MCP handshake before giving up.
GATEWAY_STARTUP_TIMEOUT = int(os.getenv("AGENTCORE_GATEWAY_STARTUP_TIMEOUT", "60"))
