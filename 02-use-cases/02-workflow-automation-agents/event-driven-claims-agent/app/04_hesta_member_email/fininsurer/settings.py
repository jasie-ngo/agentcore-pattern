"""This client's own settings: the values that belong to it, not to the core.

The core's generic settings (region, Gateway connection) are in ``core/settings.py``. Values the YAML can express directly (model
ids, guardrail id, memory id) are declared in ``config.yaml`` with ``${VAR:-default}`` interpolation; what is here is only what
Python needs at runtime.
"""

from __future__ import annotations

import os

# Minimum confidence (0-100) for a primary intent to count as confident. Below this, or an "other_unknown" intent, the case
# escalates to a person.
INTENT_CONFIDENCE_THRESHOLD = int(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "70"))

# When True, an escalated case writes a record through the MCP Gateway (create_claim + request_human_review): the human hand-off.
ENABLE_HITL_RECORD = os.getenv("ENABLE_HITL_RECORD", "true").lower() in ("1", "true", "yes")

# The sentinel the Bedrock Guardrail substitutes for blocked output. It is the same for every client (the CDK stack and
# core/pipeline.base.yaml hold it too); the reviewer agent and this package recognise an intervention by it.
GUARDRAIL_BLOCK_SENTINEL = "[GUARDRAIL_BLOCKED_ADVICE]"
