"""HESTA member-email pipeline — AgentCore Runtime entrypoint.

The whole agent system lives in ``config.yaml``: nine agents, their prompts,
their models, the Bedrock Guardrail, AgentCore Memory, the deterministic tools
and the delegate orchestration that sequences them.  This file only wraps that
config as the ASGI app AgentCore Runtime expects.

Compare with ``app/hesta-claimsagent/main.py``, which hand-wires the same
pipeline in ~390 lines of orchestration code.

Usage (local dev — server + REPL in one terminal):
    sca dev --config examples/04_hesta_member_email/config.yaml

Usage (standalone):
    python examples/04_hesta_member_email/main.py
"""

from pathlib import Path

from strands_compose_agentcore import create_app

app = create_app(Path(__file__).parent / "config.yaml")

if __name__ == "__main__":
    app.run(port=8080)
