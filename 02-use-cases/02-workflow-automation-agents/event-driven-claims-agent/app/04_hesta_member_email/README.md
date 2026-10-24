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
tools/gateway.py                →     core/gateway.py (unchanged mechanism)
memory/session.py               →     core/memory.py   (changed: gated on a verified identity, see below)
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
| `core/` | **The client-neutral core, shipped in every container.** It names no client; a test enforces that |
| `core/gateway.py` | The Gateway MCP client (Cognito M2M via AgentCore Identity) |
| `core/memory.py` | AgentCore Memory recall and write, opened only for a verified member |
| `core/factory.py` | Generic agent factory — structured-output contracts, Gateway attachment, Cedar rule pack |
| `core/cedar_pack.py`, `core/gherkin.py` | The rule-pack compiler and fail-closed loader, and the Gherkin parser they share |
| `core/settings.py` | Generic env vars: region and the Gateway connection |
| `hesta/factory.py` | HESTA's binding of the core factory to `hesta/schemas.py` |
| `hesta/schemas.py` | Pydantic structured-output contracts (verbatim port of `models.py`) |
| `hesta/taxonomy.py` | The 8 HESTA intents (verbatim port) |
| `hesta/knowledge.py` | HESTA house style + per-intent snippets (verbatim port) |
| `hesta/ingestion.py` | Email normaliser (verbatim port) |
| `hesta/settings.py` | HESTA's own thresholds and the guardrail sentinel |

---

## Run it

```bash
sca dev --config examples/04_hesta_member_email/config.yaml
```

With no AWS resources configured it still runs end to end: the Gateway agents
report that the lookup was unavailable, the memory tools recall and record nothing,
and the Writer runs without the guardrail. Unverified cases escalate to
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
`context_manager` is stateless too: it carries no session manager, and the coordinator passes it any recalled history.

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
`hesta/schemas.py` and `core/factory.py` (bound to HESTA's schemas by `hesta/factory.py`) resolves it:

```yaml
intent_identifier:
  agent_kwargs:
    structured_output: IntentResult
```

strands returns structured output through `Agent.as_tool()` as a `json` content
block, so the coordinator receives validated objects rather than prose — the same
contract the Python orchestrator had.

### AgentCore Memory → two gated tools, not a session manager

There is no `session_manager:` anywhere in the config. A session manager fixes its
actor when it is built, and a YAML-declared one is built once per session, so the
actor could only be a constant: every member's contacts were pooled under it and
recalled for everyone.

Memory is `recall_member_memory` and `remember_member_contact` (`hesta/tools.py`,
logic in `core/memory.py`), called by the coordinator after identity is checked. Each
does nothing unless `classify_verification` has **verified that member number for that
sender address**, so a different sender quoting a member's number can neither read nor
write that member's memory. The actor is the member number, and the namespaces are the
same as before: `claims/{actorId}/facts` and `claims/{actorId}/{sessionId}`. With no
`memory_id` the tools recall and record nothing.

What it does not fix: "verified" is only as strong as the sender-address match, which can
be spoofed, and the lookup fields reach `classify_verification` through the identity agent.

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

So `agent_kwargs.gateway: true` asks the factory to build the client then,
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

## Deployment profile

`DEPLOYMENT_PROFILE` is `strict` unless it is exactly `presale` (a typo means strict). **Strict** is for any real member data: a
sender who has not passed DMARC is capped at `partial`, so nothing proceeds and memory stays closed. **Presale** is an explicit
switch for demos with synthetic data only: the sender-address match counts as verified, and the report is stamped. Today nothing
supplies the authentication signal (the Trigger Lambda forwards only the body and the `From:` line), so strict cases are all
capped. See `docs/decisions/0015-spin-up-and-runtime-open-questions.md`.

## Known differences from `hesta-claimsagent`

Three, all called out here rather than papered over:

1. **Memory is opened only for a verified member (deliberately stricter).** The Python
   agent keys memory on the number found in the email text and opens it before identity is
   checked, so a sender quoting someone else's number can recall, and write into, that
   member's memory. Here identity runs first and memory follows only a verified match.
   The Python agent still has the weakness; this port does not.

2. **Explicit memory calls.** The Python agent wrote with `MemoryClient.create_event` by hand.
   Here both directions are explicit tools, because a session manager cannot take a
   per-request actor.

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
