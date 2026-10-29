"""AgentCore Runtime entrypoint — serves exactly one use case per deployment.

Each use case is a folder under ``app/`` whose ``config.yaml`` defines the whole
agent system (agents, prompts, models, memory, orchestration). Generic wiring
those configs share lives in ``app/_shared/``. This file only wraps the selected
config as the ASGI app AgentCore Runtime expects.

To switch use case, change USE_CASE below and redeploy — nothing in
``agentcore/agentcore.json`` changes. See SWITCHING_USE_CASES.md.

Usage (local dev — server + REPL in one terminal):
    sca dev --config app/hesta_agent/config.yaml

Usage (standalone):
    python app/main.py
"""

from pathlib import Path

from strands_compose_agentcore import create_app

USE_CASE = "hesta_agent"  # or "claims_agent" / "card_dispute_agent" — see SWITCHING_USE_CASES.md

app = create_app(Path(__file__).parent / USE_CASE / "config.yaml")

if __name__ == "__main__":
    app.run(port=8080)
