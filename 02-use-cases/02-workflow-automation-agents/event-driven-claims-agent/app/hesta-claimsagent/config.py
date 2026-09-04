"""Centralized configuration. ALL env var reads live here — nowhere else.

Environment variables are injected by the CDK stack at deploy time.
The L3 construct auto-generates names like AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL
and MEMORY_CLAIMSAGENTMEMORY_ID. We read both the auto-generated and explicit names.
"""

import os

# ─── Model ──────────────────────────────────────────────────────────────────
AGENT_MODEL_ID = os.getenv("AGENT_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
# Fast/cheap model for the Validation Agent (classification task, no tool use).
FAST_MODEL_ID = os.getenv("FAST_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

# ─── AWS Region ─────────────────────────────────────────────────────────────
REGION = os.getenv("AWS_REGION", "us-west-2")

# ─── Gateway ────────────────────────────────────────────────────────────────
# The L3 construct sets AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL automatically.
# We also check the explicit name passed by infra-construct for backward compat.
GATEWAY_URL = os.getenv(
    "AGENTCORE_GATEWAY_URL",
    os.getenv("AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL", ""),
)
GATEWAY_OAUTH_SCOPES = os.getenv("AGENTCORE_GATEWAY_OAUTH_SCOPES", "agentcore/invoke")

# Identity credential provider — registered via `agentcore add credential`
# during deploy. The @requires_access_token decorator uses this name to
# fetch tokens from the AgentCore Identity token vault (Secrets Manager-backed).
GATEWAY_CREDENTIAL_PROVIDER = os.getenv("AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER", "cognito-gateway-m2m")

# ─── Memory ─────────────────────────────────────────────────────────────────
# L3 construct injects MEMORY_CLAIMSAGENTMEMORY_ID; explicit fallback for manual config.
MEMORY_ID = os.getenv(
    "MEMORY_CLAIMSAGENTMEMORY_ID",
    os.getenv("AGENTCORE_MEMORY_ID", ""),
)

# Memory retrieval tuning — controls how much prior context is recalled per invocation.
MEMORY_RETRIEVAL_TOP_K = int(os.getenv("MEMORY_RETRIEVAL_TOP_K", "5"))
MEMORY_RETRIEVAL_RELEVANCE = float(os.getenv("MEMORY_RETRIEVAL_RELEVANCE", "0.5"))

# ─── Routing ────────────────────────────────────────────────────────────────
# Confidence score threshold for auto-approval. Claims with confidence >= this
# value are approved automatically; below routes to human review.
AUTO_APPROVE_THRESHOLD = int(os.getenv("AUTO_APPROVE_THRESHOLD", "80"))

# ─── HESTA pilot ──────────────────────────────────────────────────────────────
# Minimum confidence (0-100) for a primary intent to be treated as confident.
# Below this, or an "other_unknown" intent, the case escalates to human review.
INTENT_CONFIDENCE_THRESHOLD = int(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "70"))

# When True, escalated cases write a record to DynamoDB via the MCP Gateway
# (reusing create_claim + request_human_review) — the human hand-off. No new AWS
# resources: it reuses the existing Claims/Reviews tables and Gateway tools.
ENABLE_HITL_RECORD = os.getenv("ENABLE_HITL_RECORD", "true").lower() in ("1", "true", "yes")

# ─── Guardrail (no personal financial advice) ─────────────────────────────────
# Bedrock Guardrail (denied topic) injected by CDK. Attached to the Writer model so the
# agent cannot generate personal financial/product advice. Empty ⇒ app-layer controls only.
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")
# Sentinel the guardrail substitutes for blocked output (matches CDK blockedOutputsMessaging).
GUARDRAIL_BLOCK_SENTINEL = "[GUARDRAIL_BLOCKED_ADVICE]"

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ─── Model routing override (canary testing) ──────────────────────────────────
# Optional DynamoDB table for per-role model overrides, layered beneath the
# AGENT_MODEL_ID/FAST_MODEL_ID env-var defaults above. Empty ⇒ disabled, use env
# vars only (today's behaviour, unchanged). Table schema: partition key `role`
# ("fast" | "strong"), attributes `primaryModelId`, `canaryModelId` (optional),
# `canaryPercent` (0-100, optional).
MODEL_ROUTING_TABLE = os.getenv("MODEL_ROUTING_TABLE", "")

import hashlib
import logging

_model_routing_log = logging.getLogger(__name__)


def _get_model_routing_item(role: str) -> dict | None:
    """Fetch the override item for a role. Isolated for easy test mocking."""
    import boto3

    table = boto3.resource("dynamodb", region_name=REGION).Table(MODEL_ROUTING_TABLE)
    response = table.get_item(Key={"role": role})
    return response.get("Item")


def resolve_model_variant(role: str, seed: str) -> tuple[str, str]:
    """Resolve which model id to use for a role, honouring any canary override.

    Args:
        role: "fast" or "strong" — matches agents/base.py's existing fast/strong split.
        seed: a stable per-case identifier (e.g. the case's actor/session id) used to
            deterministically bucket the same case into the same variant every time it's
            evaluated, rather than an independent random draw per call.

    Returns:
        (model_id, variant_label) where variant_label is "primary" or "canary".
        Never raises — any failure degrades to the existing env-var default with
        variant_label="primary", matching today's behaviour exactly.
    """
    default_id = FAST_MODEL_ID if role == "fast" else AGENT_MODEL_ID
    if not MODEL_ROUTING_TABLE:
        return default_id, "primary"

    try:
        item = _get_model_routing_item(role)
    except Exception as exc:  # noqa: BLE001 — routing override is best-effort
        _model_routing_log.warning("Model routing lookup failed for role=%s (using default): %s", role, exc)
        return default_id, "primary"

    if not item:
        return default_id, "primary"

    primary_id = item.get("primaryModelId") or default_id
    canary_id = item.get("canaryModelId")
    canary_pct = int(item.get("canaryPercent", 0) or 0)

    if not canary_id or canary_pct <= 0:
        return primary_id, "primary"
    if canary_pct >= 100:
        return canary_id, "canary"

    bucket = int(hashlib.sha256(f"{role}:{seed}".encode()).hexdigest(), 16) % 100
    if bucket < canary_pct:
        return canary_id, "canary"
    return primary_id, "primary"
