# Switching use cases

One AgentCore Runtime (`multiusecaseagent`) serves one use case at a time. Which
one is decided by a single line in [`app/main.py`](app/main.py):

```python
USE_CASE = "hesta_agent"  # change to "claims_agent" to switch use case
```

`agentcore/agentcore.json` is **not** edited to switch. It points the runtime at
the whole `app/` folder (`codeLocation: "app"`, `entrypoint: "main.py"`), so every
use case and `app/_shared/` are always in the container.

## Layout

```
app/
├── main.py           # entrypoint — the USE_CASE line is the only switch
├── Dockerfile        # one image for every use case
├── pyproject.toml    # one dependency set (uv.lock pinned)
├── _shared/          # generic plumbing, no domain logic — change once, affects all
│   ├── factory.py      agent builder: structured output + Gateway attachment
│   ├── gateway.py      MCP Gateway client (Cognito M2M via AgentCore Identity)
│   ├── memory.py       AgentCore Memory session manager + local fallback
│   └── settings.py     generic env vars (region, Gateway)
├── hesta_agent/      # HESTA member-email: config.yaml + its own Python
└── claims_agent/     # insurance claims:   config.yaml + its own Python
```

| Change | Where |
|---|---|
| Prompt, model, agent, orchestration for one use case | `app/<use_case>/config.yaml` |
| Domain logic / deterministic tools for one use case | `app/<use_case>/*.py` |
| Gateway auth, Memory, agent factory (all use cases) | `app/_shared/*.py` |
| Which use case is deployed | `app/main.py` → `USE_CASE` |
| New Lambda tool, Gateway target, Memory strategy | `agentcore/agentcore.json` (infrastructure, not a switch) |

A use case's `config.yaml` references the shared code by relative path, e.g.
`type: ../_shared/factory.py:agent` and
`session_manager.type: ../_shared/memory.py:session_manager`.

## Switch procedure

1. **Run locally first (optional).** Both configs run locally without touching
   `app/main.py` at all — use this to verify a change before the edit + redeploy:
   ```bash
   sca dev --config app/hesta_agent/config.yaml
   sca dev --config app/claims_agent/config.yaml
   ```
2. Edit `app/main.py`: set `USE_CASE = "claims_agent"` (or `"hesta_agent"`).
3. Redeploy. This fully replaces whatever was running before — it's a switch,
   not an addition:
   ```bash
   agentcore deploy --target dev --yes
   ```
4. Verify with the matching payload, not the other use case's
   (`agentcore dev --runtime multiusecaseagent` for a local runtime check):
   - **HESTA:** invoke with a `prompt` from `app/hesta_agent/sample_emails/*.json`.
   - **Claims:** `python3 scripts/test_invoke.py --region <region> --prompt 'File a claim for POL-12345. $5000 storm damage. Claimant email: john@example.com'`

Both use cases share the same Gateway tools, DynamoDB tables and AgentCore Memory
resource; they differ only in their `config.yaml` (Memory actor ids differ, so
their recalled history does not mix).

## Adding a use case

1. Create `app/<name>/` with `__init__.py`, `config.yaml`, and any `schemas.py` /
   `tools.py` it needs. Use `type: ../_shared/factory.py:agent` for agents and
   `structured_output: <name>.schemas:<Class>` for structured output.
2. Python files in the use case that import siblings put `app/` on `sys.path`
   first (see the top of `claims_agent/tools.py`) and import as
   `from <name>.<module> import ...`.
3. Add any new dependency to `app/pyproject.toml` and run `uv lock` in `app/`.
4. Point `USE_CASE` at it.

## Known limitation: payload fields other than `prompt`

`strands-compose-agentcore` passes only the payload's `prompt` to the entry
agent. The Trigger Lambda's top-level `claimant_email` and `source` keys are
dropped, so the claims pipeline only sees a claimant email that appears inside
the prompt text (true for JSON claim files, whose content is embedded in the
prompt; not for `.eml` claims, whose `From:` header is sent separately). Folding
those fields into `prompt` in `lambdas/trigger/handler.py` closes the gap.
