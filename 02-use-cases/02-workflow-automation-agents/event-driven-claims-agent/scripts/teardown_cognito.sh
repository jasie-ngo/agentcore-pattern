#!/bin/bash
set -euo pipefail

# ============================================================================
# Cognito User Pool Teardown
#
# Deletes Cognito resources that were created by setup_cognito.sh.
# Only runs if .cognito-state.json exists (proving this script created them).
# ============================================================================

# Usage: ./scripts/teardown_cognito.sh [--client <slug>] [--plan]
# With --client it acts on build/<slug>/.cognito-state.json only, and refuses a state file recorded for a different pool.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/ctx.sh"
ctx_parse_args "$@"
ctx_load
PROJECT_DIR="$CTX_PROJECT_DIR"
STATE_FILE="$CTX_COGNITO_STATE"
if [ "$PLAN" = true ]; then
  ctx_print_plan "delete the Cognito pool recorded in $STATE_FILE"
  exit 0
fi

if [ ! -f "$STATE_FILE" ]; then
  echo "ℹ️  No .cognito-state.json found — Cognito was not created by setup_cognito.sh."
  echo "   Nothing to tear down."
  exit 0
fi

REGION=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['region'])")
USER_POOL_ID=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['user_pool_id'])")
DOMAIN_PREFIX=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['domain_prefix'])")

# The state file must be this deployment's own: never delete a pool recorded for a different client.
RECORDED_POOL_NAME=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('user_pool_name',''))")
if [ -n "$RECORDED_POOL_NAME" ] && [ "$RECORDED_POOL_NAME" != "$CTX_COGNITO_POOL" ]; then
  echo "ERROR: $STATE_FILE records pool \"$RECORDED_POOL_NAME\" but this deployment's pool is \"$CTX_COGNITO_POOL\". Not deleting." >&2
  exit 1
fi

echo "🗑️  Tearing down Cognito resources created by setup_cognito.sh..."
echo "   Region: $REGION"
echo "   User Pool: $USER_POOL_ID"
echo ""

# Delete domain first (required before pool deletion)
echo "   Deleting domain: $DOMAIN_PREFIX"
aws cognito-idp delete-user-pool-domain \
  --domain "$DOMAIN_PREFIX" \
  --user-pool-id "$USER_POOL_ID" \
  --region "$REGION" 2>/dev/null || true

# Delete user pool (cascades: resource servers, clients are deleted with pool)
echo "   Deleting user pool: $USER_POOL_ID"
aws cognito-idp delete-user-pool \
  --user-pool-id "$USER_POOL_ID" \
  --region "$REGION"

# Remove state file
rm -f "$STATE_FILE"

echo ""
echo "✅ Cognito resources deleted."
echo "   Note: .env still contains the old values — update or recreate before next deploy."
