# Switching between HESTA and Claims (one runtime, manual swap)

This project deploys **one** AgentCore Runtime — `multiusecaseagent` — that can
run either use case. Only one is live at a time. `agentcore.json` is never
touched to switch; only `app/main.py` changes.

## Step 1 — edit `app/main.py`

```python
from pathlib import Path
from strands_compose_agentcore import create_app

app = create_app(Path(__file__).parent / "hesta_agent" / "config.yaml")
```

To switch to claims, change the one folder name:

```python
app = create_app(Path(__file__).parent / "claims_agent" / "config.yaml")
```

That's the entire change. `agentcore.json`'s `codeLocation` (`"app"`) and
`entrypoint` (`"main.py"`) stay fixed — they already cover both use cases,
since `app/` contains `_shared/`, `hesta_agent/`, and `claims_agent/` together.

## Step 2 — redeploy

```bash
agentcore deploy --target dev --yes
```

This fully replaces whatever was running before. That's expected — it's a
switch, not an addition.

## Step 3 — verify

```bash
agentcore dev --runtime multiusecaseagent
```

Test with the matching sample payload (`app/hesta_agent/sample_emails/*.json`
or a sample claim), not the other use case's.

## Before switching, run locally first

Both configs can be run locally without touching `app/main.py` at all:

```bash
sca dev --config app/hesta_agent/config.yaml
sca dev --config app/claims_agent/config.yaml
```

Use this to verify a change before committing to the `main.py` edit + redeploy.
