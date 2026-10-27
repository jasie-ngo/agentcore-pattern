#!/bin/bash
set -euo pipefail

# ============================================================================
# Event-Driven Claims Agent — One-Command Teardown
# Usage: ./destroy.sh [region] [--client <slug>] [--plan]
# Example: ./destroy.sh us-west-2
#          ./destroy.sh ap-southeast-2 --client acme --plan   # print the stack it would destroy, touch nothing
#
# With --client it destroys that client's stack only (build/<slug>); without, the original single-client stack.
#
# Destroys ALL resources created by deploy.sh:
# - AgentCore Runtime, Gateway, Memory, PolicyEngine, OnlineEval
# - Infrastructure (DynamoDB, S3, SNS, Cognito, EventBridge, Lambda)
#
# S3 buckets and DynamoDB tables have RemovalPolicy.DESTROY + autoDeleteObjects,
# so all data is permanently deleted. This is NOT reversible.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/scripts/lib/ctx.sh"
ctx_parse_args "$@"
REGION="${POSITIONAL[0]:-us-west-2}"
ctx_load   # refuses a client whose project is missing or belongs to another client
if [ "$PLAN" = true ]; then
  ctx_print_plan "destroy stack $CTX_STACK_NAME in $REGION"
  exit 0
fi
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"
if [ -n "$CTX_CLIENT" ]; then export AGENTCORE_CLIENT="$CTX_CLIENT"; fi

# Use Finch or Docker for container builds
export CDK_DOCKER="${CDK_DOCKER:-docker}"

cd "$CTX_PROJECT_DIR"

echo "🗑️  Destroying Claims Agent in $REGION (stack $CTX_STACK_NAME)..."
echo ""

# Ensure aws-targets.json exists with correct region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ ! -f agentcore/aws-targets.json ]; then
  cat > agentcore/aws-targets.json <<EOF
[
  {
    "name": "dev",
    "account": "$ACCOUNT_ID",
    "region": "$REGION"
  }
]
EOF
  echo "  Generated aws-targets.json (account: $ACCOUNT_ID, region: $REGION)"
  echo ""
fi

# Install CDK dependencies if needed
cd agentcore/cdk
if [ ! -d "node_modules" ]; then
  echo "📦 Installing CDK dependencies..."
  npm install --quiet
fi

# Destroy via CDK
echo "💥 Destroying stack $CTX_STACK_NAME..."
cdk destroy --all --force
cd ../..
echo ""

echo "✅ Done! All Claims Agent resources destroyed in $REGION (stack $CTX_STACK_NAME)."
echo ""
echo "To redeploy: ./deploy.sh $REGION$CTX_CLIENT_FLAG"
