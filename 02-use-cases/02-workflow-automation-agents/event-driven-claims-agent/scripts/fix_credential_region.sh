#!/bin/bash
# Legacy fallback: the `cognito-gateway-m2m` credential provider is now a CDK-managed
# OAuth2CredentialProvider resource (see agentcore/cdk/lib/cdk-stack.ts), so a normal
# `agentcore deploy` / `cdk deploy` reconciles its discovery URL against the current
# region automatically, and this script should no longer be needed in the ordinary case.
# It's kept as a manual escape hatch for repairing a stale discovery URL directly via
# the control-plane API, without waiting on a full stack deploy.
#
# Usage: ./scripts/fix_credential_region.sh [region]
# Reads Cognito values from .env (COGNITO_DISCOVERY_URL, AGENTCORE_GATEWAY_CLIENT_ID,
# AGENTCORE_GATEWAY_CLIENT_SECRET). The secret is never printed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REGION="${1:-us-east-1}"
PROVIDER_NAME="${AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER:-cognito-gateway-m2m}"

# Load Cognito values written by setup_cognito.sh
set -a
# shellcheck disable=SC1091
source "$PROJECT_DIR/.env"
set +a

: "${COGNITO_DISCOVERY_URL:?COGNITO_DISCOVERY_URL not set in .env}"
: "${AGENTCORE_GATEWAY_CLIENT_ID:?AGENTCORE_GATEWAY_CLIENT_ID not set in .env}"
: "${AGENTCORE_GATEWAY_CLIENT_SECRET:?AGENTCORE_GATEWAY_CLIENT_SECRET not set in .env}"

echo "Updating credential provider '$PROVIDER_NAME' in $REGION"
echo "  discoveryUrl -> $COGNITO_DISCOVERY_URL"
echo "  clientId     -> $AGENTCORE_GATEWAY_CLIENT_ID"

CONFIG=$(cat <<JSON
{
  "customOauth2ProviderConfig": {
    "oauthDiscovery": { "discoveryUrl": "$COGNITO_DISCOVERY_URL" },
    "clientId": "$AGENTCORE_GATEWAY_CLIENT_ID",
    "clientSecret": "$AGENTCORE_GATEWAY_CLIENT_SECRET"
  }
}
JSON
)

aws bedrock-agentcore-control update-oauth2-credential-provider \
  --name "$PROVIDER_NAME" \
  --credential-provider-vendor CustomOauth2 \
  --oauth2-provider-config-input "$CONFIG" \
  --region "$REGION" \
  --query "name" --output text

echo "✅ Credential provider updated. Verifying discovery URL..."
aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name "$PROVIDER_NAME" --region "$REGION" \
  --query "oauth2ProviderConfigOutput.customOauth2ProviderConfig.oauthDiscovery.discoveryUrl" \
  --output text
