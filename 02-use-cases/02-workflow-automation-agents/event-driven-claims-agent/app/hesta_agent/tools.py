"""Local tools for the HESTA use case — bindings only, no logic.

Every tool is built by the framework from this folder's YAML forms:

* ``normalize_email``   ← ``ingestion.yaml``  (raw text → envelope)
* ``verify_identity``   ← ``decisions.yaml``  (AI-003 verification policy)
* ``check_attachments`` ← ``decisions.yaml``  (AI-004 expected document)
* ``route_case``        ← ``decisions.yaml``  (human-in-the-loop gate)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from _shared.local_tools import decision_table_tool, ingestion_tool

HERE = Path(__file__).parent

normalize_email = ingestion_tool(HERE / "ingestion.yaml", use_case="hesta_agent")
verify_identity = decision_table_tool(HERE / "decisions.yaml", name="verify_identity")
check_attachments = decision_table_tool(HERE / "decisions.yaml", name="check_attachments")
route_case = decision_table_tool(HERE / "decisions.yaml", name="route_case")
