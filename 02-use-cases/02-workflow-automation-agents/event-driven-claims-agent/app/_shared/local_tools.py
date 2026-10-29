"""Tool builders — turn a use case's YAML forms into agent tools, no code needed.

A use case binds them in its ``tools.py`` (one line each) and lists them in
``config.yaml`` like any other tool:

    # app/<use_case>/tools.py
    from _shared.local_tools import decision_table_tool, ingestion_tool
    HERE = Path(__file__).parent
    normalize_email = ingestion_tool(HERE / "ingestion.yaml", use_case="<use_case>")
    decide_dispute = decision_table_tool(HERE / "decisions.yaml")

* ``ingestion_tool``      — normalises raw message text into the framework
  envelope with the use case's ``ingestion.yaml`` (for direct invocations; S3
  uploads are already normalised by the Trigger Lambda).
* ``decision_table_tool`` — one deterministic decision from a decision table
  (see ``_shared/decision_table.py``). One YAML file may hold several tables
  under ``tables:``; pick one with ``name=``. ``${VAR:-default}`` in the file is
  filled from the environment, so a threshold can be tuned per deployment.

``load_table`` returns the validated table itself, for deterministic code that
applies a decision without an agent in between.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import yaml
from strands.tools.tools import PythonAgentTool

from _shared import decision_table
from _shared.ingestion import load_config, normalize

_JSON_TYPES = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean"}
_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _result(tool_use: dict, payload: dict, status: str = "success") -> dict:
    return {"toolUseId": tool_use["toolUseId"], "status": status, "content": [{"json": payload}]}


def load_table(path, name: str | None = None) -> dict:
    """Read one decision table from a YAML file (env placeholders filled in) and validate it."""
    text = _ENV_VAR.sub(
        lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), Path(path).read_text(encoding="utf-8")
    )
    data = yaml.safe_load(text) or {}
    tables = data.get("tables") or [data]
    table = next((t for t in tables if name in (None, t.get("name"))), None)
    if table is None:
        raise ValueError(f"{path}: no decision table named '{name}'")
    decision_table.validate(table)
    return table


def decision_table_tool(path, name: str | None = None) -> PythonAgentTool:
    """Build an agent tool that applies one decision table from a YAML file."""
    table = load_table(path, name)

    outcomes = sorted({r["then"]["outcome"] for r in table.get("rules", [])} | {table["default"]["outcome"]})
    spec = {
        "name": table["name"],
        "description": " ".join(
            [
                table["description"].strip(),
                "Deterministic: the decision is final; report it unchanged.",
                f"Possible outcomes: {', '.join(outcomes)}.",
            ]
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    field: {
                        "type": _JSON_TYPES[(s or {}).get("type", "string")],
                        "description": (s or {}).get("description", field),
                    }
                    for field, s in (table.get("inputs") or {}).items()
                },
                "required": [],
            }
        },
    }

    def run(tool_use, **_invocation_state):
        return _result(tool_use, decision_table.evaluate(table, tool_use.get("input") or {}))

    return PythonAgentTool(table["name"], spec, run)


def ingestion_tool(path, *, use_case: str, name: str = "normalize_email") -> PythonAgentTool:
    """Build an agent tool that normalises raw text with an ``ingestion.yaml``."""
    config = load_config(path)
    spec = {
        "name": name,
        "description": (
            "Normalise raw inbound message text into the standard envelope: sender, subject, the "
            "customer id found in it (id_for_lookup), the latest message without banners, footers or "
            "quoted history, and the attachment count. Only needed when the input is raw text — an "
            "input that already has envelope_version is already normalised."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "raw": {"type": "string", "description": "The raw message text exactly as received"},
                    "sender_email": {"type": "string", "description": "Sender address, if known"},
                    "subject": {"type": "string", "description": "Subject line, if known"},
                    "source": {"type": "string", "description": "Where the message came from, if known"},
                },
                "required": ["raw"],
            }
        },
    }

    def run(tool_use, **_invocation_state):
        args = tool_use.get("input") or {}
        envelope = normalize(
            args.get("raw", ""),
            config,
            use_case=use_case,
            sender_email=args.get("sender_email") or None,
            subject=args.get("subject") or None,
            source=args.get("source") or None,
        )
        envelope.pop("raw_text", None)
        return _result(tool_use, envelope)

    return PythonAgentTool(name, spec, run)
