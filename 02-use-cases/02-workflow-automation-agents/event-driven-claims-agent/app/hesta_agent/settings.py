"""HESTA-only configuration.

Generic plumbing (region, Gateway connection) lives in ``_shared/settings.py``.
What is left here is only what the HESTA deterministic tools need at runtime.
"""

from __future__ import annotations

import os

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
