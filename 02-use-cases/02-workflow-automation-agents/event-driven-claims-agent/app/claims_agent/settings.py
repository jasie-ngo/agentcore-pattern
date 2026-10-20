"""Claims-only configuration.

Generic plumbing (region, Gateway connection) lives in ``_shared/settings.py``.
"""

from __future__ import annotations

import os

# ─── Routing ────────────────────────────────────────────────────────────────
# Confidence score threshold for auto-approval. Claims with confidence >= this
# value are approved automatically; below routes to human review.
AUTO_APPROVE_THRESHOLD = int(os.getenv("AUTO_APPROVE_THRESHOLD", "80"))
