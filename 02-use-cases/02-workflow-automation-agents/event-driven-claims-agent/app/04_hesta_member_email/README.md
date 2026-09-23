# 04 — HESTA Member-Email Pipeline (YAML-first)

> A nine-agent production pipeline — MCP Gateway tools, AgentCore Memory, a
> Bedrock Guardrail, structured-output contracts and a human-in-the-loop gate —
> composed entirely in `config.yaml`.

This example rebuilds [`event-driven-claims-agent/app/hesta-claimsagent`](../../event-driven-claims-agent/app/hesta-claimsagent)
as a strands-compose config. Same agents, same prompts, same AgentCore Gateway
tools over MCP, same AgentCore Memory, same guardrail, same routing rules.

The difference is where the wiring lives. The hand-written agent orchestrates in
Python: ~390 lines in `main.py` that build agents, call them in order, format the
output and manage the MCP session. Here the orchestration is a `delegate` block
in YAML, and `main.py` is three lines.

```
hesta-claimsagent                     04_hesta_member_email
─────────────────────────────         ─────────────────────────────
main.py            390 lines    →     main.py              3 lines
agents/*.py  (9)   ~700 lines   →     config.yaml (declarative)
routing.py, ingestion/,               hesta/tools.py  (same logic,
knowledge/, intents/            →     exposed as @tool functions)
tools/gateway.py                →     hesta/gateway.py (unchanged mechanism)
memory/session.py               →     hesta/memory.py  (unchanged mechanism)
```

---

## The pipeline

Flow is unchanged: **UNDERSTAND → DECIDE → EXECUTE → LEARN**. A `delegate`
orchestration makes the coordinator call each specialist as a tool, in order.

```
                        coordinator  (delegate entry)
                        tools: normalize_email, route_decision
                              │
   ┌──────────────────────────┼──────────────────────────────┐
   │ 1 UNDERSTAND             │ 2 DECIDE                     │ 3 EXECUTE
   │                          │                              │
   ├─ intent_identifier       ├─ identity_profiler  ──▶ MCP  ├─ writer ──▶ 🛡 guardrail
   │    AI-001  IntentResult  │    AI-003  MemberProfile     │    AI-011  DraftEmail
   │                          │                              │
   ├─ context_manager ──▶ 🧠  ├─ attachment_validator        ├─ reviewer_editor
   │    AI-002  CaseSummary   │    AI-004  AttachmentAssess. │    AI-012  ReviewResult
   │                          │                              │
   └─ case_status_checker ▶MCP└─ empathy                     └─ human_handoff ──▶ MCP
        CaseStatusResult           AI-005  EmpathyAssessment       create_claim +
        (status enquiries only)                                    request_human_review
                                   ▼
                            route_decision  (deterministic gate)

   🧠 AgentCore Memory     MCP  AgentCore Gateway (streamable HTTP + Cognito M2M)
```

The draft reply is **displayed** for HESTA staff to review and send. Nothing is
ever emailed to the member. Escalation writes a DynamoDB record through the
existing Gateway tools — no new AWS resources.

## Files

| File | Purpose |
|------|---------|
| `config.yaml` | **The whole system** — models, 9 agents + coordinator, prompts, guardrail, memory, orchestration |
| `main.py` | `create_app(config.yaml)` — nothing else |
| `pyproject.toml` | Dependencies for deployment |
| `.env.local.example` | Every environment variable, with the same names and defaults as `hesta-claimsagent/config.py` |
| `sample_emails/*.json` | Three payloads to try: contact form, hardship with attachment, personal-advice request |
| `hesta/tools.py` | The deterministic steps, as `@tool` functions |
| `hesta/gateway.py` | The Gateway MCP client (Cognito M2M via AgentCore Identity) |
| `hesta/memory.py` | The AgentCore Memory session manager, with local fallback |
| `hesta/factory.py` | Agent factory — structured-output contracts + Gateway attachment |
| `hesta/schemas.py` | Pydantic structured-output contracts (verbatim port of `models.py`) |
| `hesta/taxonomy.py` | The 8 HESTA intents (verbatim port) |
| `hesta/knowledge.py` | HESTA house style + per-intent snippets (verbatim port) |
| `hesta/ingestion.py` | Email normaliser (verbatim port) |
| `hesta/settings.py` | The env vars Python still needs at runtime |

---

## Run it

```bash
sca dev --config examples/04_hesta_member_email/config.yaml
```

With no AWS resources configured it still runs end to end: the Gateway agents
report that the lookup was unavailable, AgentCore Memory falls back to local file
sessions, and the Writer runs without the guardrail. Unverified cases escalate to
a human, which is the correct behaviour.

To feed it a sample email, paste the `prompt` value from one of the
`sample_emails/*.json` files into the REPL, or POST the whole file:

```bash
curl -s localhost:8080/invocations \
  -H 'Content-Type: application/json' \
  -H 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: local-dev-session-0000000000000000000000000000000000' \
  -d @examples/04_hesta_member_email/sample_emails/hardship_with_attachment.json
```

For the full pipeline, point it at your deployed resources:

```bash
cp examples/04_hesta_member_email/.env.local.example examples/04_hesta_member_email/.env.local
# fill in AGENTCORE_GATEWAY_URL, MEMORY_CLAIMSAGENTMEMORY_ID, GUARDRAIL_ID
export LOCAL_DEV=1
sca dev --config examples/04_hesta_member_email/config.yaml
```

## Deploy it

Same three commands as [example 02](../02_deploy/README.md) — the config is the
only thing that differs:

```bash
agentcore add agent \
  --type byo \
  --name hesta_member_email \
  --code-location examples/04_hesta_member_email \
  --entrypoint main.py \
  --language Python \
  --framework Strands \
  --model-provider Bedrock
agentcore deploy
```

The agent needs the same deploy-time wiring as `hesta-claimsagent`:

- a Cognito M2M credential provider named by `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER`
  (`agentcore add credential`),
- `AGENTCORE_GATEWAY_URL` (or `AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL`) pointing at
  the Gateway,
- `MEMORY_CLAIMSAGENTMEMORY_ID` for AgentCore Memory,
- `GUARDRAIL_ID` / `GUARDRAIL_VERSION` for the no-personal-advice guardrail.

---

## What moved where

### Agents → `agents:`

Each of the nine agents is one YAML block: model, description, prompt, tools,
structured-output contract. The prompts are copied verbatim from the Python
agents, including the intent taxonomy that `hesta.taxonomy.render_for_prompt()`
used to generate at import time — it is now inlined in
`agents.intent_identifier.system_prompt`. Regenerate it after editing `INTENTS`:

```bash
python -c "import sys; sys.path.insert(0,'.'); from hesta import taxonomy; print(taxonomy.render_for_prompt())"
```

### Cost-based model routing → `models:`

`base.py`'s `fast=True/False` switch becomes three named models. `guarded` is the
Writer's model with the Bedrock Guardrail attached:

```yaml
guarded:
  provider: bedrock
  model_id: ${primary_model}
  params:
    guardrail_id: ${GUARDRAIL_ID:-}
    guardrail_redact_output: true
    guardrail_redact_output_message: "[GUARDRAIL_BLOCKED_ADVICE]"
```

`GUARDRAIL_ID` unset means `BedrockModel` omits `guardrailConfig` entirely, so the
pilot degrades to app-layer controls exactly as `_build_model(guarded=...)` did.

### Orchestrator → `orchestrations:`

`main.py`'s phase sequence becomes a `delegate` orchestration plus the
coordinator's prompt. `preserve_context: false` on the stateless specialists
matches `structured_output_async`, which worked from a temporary message list.
`context_manager` uses `true` because it carries a session manager.

The coordinator's prompt also carries the exact markdown report structure that
`main.py`'s `_fmt_*` helpers produced, so the output looks the same.

### Deterministic steps → `@tool` functions

The steps that were plain Python become tools on the agent that used them. The
logic is a straight port, verified against the originals:

| `hesta/tools.py` | Replaces |
|------------------|----------|
| `normalize_email` | `ingestion/email_normalizer.py` |
| `assess_attachments` | `agents/attachment_validation.py` |
| `classify_verification` | the verification policy in `agents/identity_profiling.py` |
| `filter_member_cases` | the filter in `agents/case_status.py` |
| `route_decision` | `routing.py` |
| `hesta_knowledge`, `identity_verification_block` | `knowledge/hesta_snippets.py` |

This keeps the decisions deterministic. `route_decision` is not a judgement call
the model makes — it is the same gate, with the same `INTENT_CONFIDENCE_THRESHOLD`,
returning the same reasons. The agents' prompts tell them to return its verdict
unchanged.

### Structured output → `agent_kwargs.structured_output`

Every specialist keeps its Pydantic contract from `models.py`. `Agent` needs a
class and YAML can only carry a name, so the config names one from
`hesta/schemas.py` and `hesta/factory.py` resolves it:

```yaml
intent_identifier:
  agent_kwargs:
    structured_output: IntentResult
```

strands returns structured output through `Agent.as_tool()` as a `json` content
block, so the coordinator receives validated objects rather than prose — the same
contract the Python orchestrator had.

### AgentCore Memory → `session_manager:`

Declared at the root and opted out of with `session_manager: ~` by every agent
except `context_manager`, mirroring the hand-written agent where only AI-002 got
a session manager. `hesta/memory.py` keeps the graceful degradation: with a
`memory_id` it binds the same `claims/{actorId}/facts` (SEMANTIC) and
`claims/{actorId}/{sessionId}` (SUMMARIZATION) namespaces; without one it returns
a local `FileSessionManager`.

---

## Why the Gateway is not a `mcp_clients:` block

The AgentCore Gateway is reached over MCP (streamable HTTP) with a Cognito M2M
bearer token that AgentCore Identity issues. That token cannot be declared in
YAML, and the reason is a lifecycle one:

- `mcp_clients:` header values are interpolated when the YAML is parsed, and
  `create_app()` parses at **import** time. No request exists yet, so
  `BedrockAgentCoreContext._workload_access_token` is unset and the M2M exchange
  fails — and even if it succeeded there would be no way to refresh an expiring
  token.
- Agent factories run inside `load(app_config, session_id=...)`, which
  `strands-compose-agentcore` calls from the `/invocations` entrypoint — the
  request thread, with the workload identity populated.

So `agent_kwargs.gateway: true` asks `hesta/factory.py` to build the client then,
and hands it to the agent as a tool provider. This is the same mechanism
`hesta-claimsagent` used, and the same reason it built the client inside its async
entrypoint.

What changes is who calls the tools. The hand-written agent called
`lookup_policy` and friends imperatively from `main.py`. Here the client is a
live MCP tool provider, so the agent sees the real Gateway tool list
(`policy-lookup___lookup_policy`, `list-pending-claims___list_pending_claims`,
`create-claim___create_claim`, `human-review___request_human_review`) and calls
it itself. The prompts name the bare tool and tell the agent to match on the
suffix, so the `___` namespacing needs no static map. Determinism is preserved by
keeping the *policy* in `classify_verification` / `filter_member_cases` — the
agent fetches, the tool decides.

`continue_on_error=True` on the client means a Gateway outage logs and yields no
tools rather than failing the invocation, matching the original's
"degrade gracefully, surface the reason" behaviour.

**If your MCP server takes a static token or no auth**, none of this applies —
declare it in YAML and list it under each agent's `mcp:`. `config.yaml` has the
block commented out at the bottom.

---

## Known differences from `hesta-claimsagent`

Three, all called out here rather than papered over:

1. **Memory actor is static.** The Python agent keyed the AgentCore Memory actor
   per invocation on the member/policy number. A YAML-declared session manager is
   resolved once per session, so `actor_id` is the agent name. Session-scoped
   recall is unchanged; cross-email recall is scoped to the agent rather than to
   the individual member.

2. **No explicit `create_event` write.** `memory/session.py` called
   `MemoryClient.create_event` by hand because `structured_output_async` never
   fired the session manager's write hooks. Here the session manager is attached
   normally and strands drives it.

3. **`account_type` on a malformed record.** When a DynamoDB record has an empty
   `policy_type`, the Python version returned `""` and this one returns `null`.
   Everything else in `classify_verification` is identical — the ports of
   `normalize_email`, `assess_attachments`, `route_decision`,
   `classify_verification` and `filter_member_cases` were diffed against the
   originals across their branches.

## Where the Python lives, and why

Only three things in `hesta/` are not a verbatim port:

| File | Why it exists |
|------|---------------|
| `factory.py` | `structured_output_model` needs a class, not a string; the Gateway client must be built on the request thread |
| `memory.py` | Graceful fallback when AgentCore Memory is not deployed — `provider: agentcore` requires a non-empty `memory_id` |
| `tools.py` | Wraps the ported deterministic logic as `@tool` functions so YAML can reference it |

Everything else — `schemas.py`, `taxonomy.py`, `knowledge.py`, `ingestion.py` —
is the original code, moved.
