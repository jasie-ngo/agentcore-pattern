"""Local tools for the card-dispute use case — bindings only, no logic.

Both tools are built by the framework from this folder's YAML forms:

* ``normalize_email`` ← ``ingestion.yaml``  (raw text → envelope)
* ``decide_dispute``  ← ``decisions.yaml``  (the bank's decision table)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from _shared.local_tools import decision_table_tool, ingestion_tool

HERE = Path(__file__).parent

normalize_email = ingestion_tool(HERE / "ingestion.yaml", use_case="card_dispute_agent")
decide_dispute = decision_table_tool(HERE / "decisions.yaml")
