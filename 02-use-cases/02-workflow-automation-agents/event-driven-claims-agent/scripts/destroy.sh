#!/bin/bash
set -euo pipefail

# ============================================================================
# Event-Driven Claims Agent — Full Teardown
#
# Usage: ./scripts/destroy.sh [region] [--client <slug>] [--sweep-orphans] [--plan]
#
# With --client it tears down that client's stack, credential and Cognito pool only, from build/<slug>.  The sweep of leftover
# AgentCore resources matches by NAME, so for a client it only runs with --sweep-orphans, and then matches the client's project
# name as a whole word (never "anything containing claims").  --plan prints what would be acted on and touches nothing.
#
# Destroys everything created by deploy.sh in one shot:
# - CloudWatch observability deliveries
# - CloudFormation stack (with DELETE_FAILED auto-recovery)
# - Orphaned AgentCore control-plane resources (Gateway, Runtime, Memory, etc.)
# - Cognito User Pool (if created by setup_cognito.sh)
# - Local state files
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/ctx.sh"
ctx_parse_args "$@"
REGION="${POSITIONAL[0]:-${AWS_REGION:-us-west-2}}"
ctx_load   # refuses a client whose project is missing or belongs to another client
PROJECT_DIR="$CTX_PROJECT_DIR"

if [ "$PLAN" = true ]; then
  ctx_print_plan "tear down stack $CTX_STACK_NAME in $REGION"
  if [ -n "$CTX_CLIENT" ] && [ "$SWEEP_ORPHANS" != true ]; then
    echo ""
    echo "  Orphan sweep: SKIPPED (add --sweep-orphans to also delete leftover AgentCore resources named $CTX_PROJECT...)"
  else
    echo ""
    echo "  Orphan sweep: ON"
  fi
  exit 0
fi

echo "🗑️  Tearing down Claims Agent in $REGION (stack $CTX_STACK_NAME)..."
echo ""

# Step 1: Observability (non-critical — skip if already gone)
echo "🔭 Step 1: Removing observability deliveries..."
python3 "$SCRIPT_DIR/disable_observability.py" --region "$REGION" \
  --stack-name "$CTX_STACK_NAME" 2>/dev/null || \
  echo "   Skipped (already removed or never enabled)"
echo ""

# Step 2: Stack + orphans + Cognito + local state (all handled by one script)
echo "💥 Step 2: Destroying stack and cleaning up resources..."
SWEEP_ARG=()
if [ "$SWEEP_ORPHANS" = true ]; then SWEEP_ARG=(--sweep-orphans); fi
python3 "$SCRIPT_DIR/cleanup_agentcore.py" --region "$REGION" --project-dir "$PROJECT_DIR" \
  ${CTX_ARGS[@]+"${CTX_ARGS[@]}"} ${SWEEP_ARG[@]+"${SWEEP_ARG[@]}"}
echo ""

echo "✅ Teardown complete!"
echo "   All AWS resources destroyed. Local .env preserved (delete manually if desired)."
