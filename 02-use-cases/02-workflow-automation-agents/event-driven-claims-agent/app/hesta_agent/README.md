# HESTA Member-Email Pipeline (YAML-first)

> A nine-agent pipeline — MCP Gateway tools, AgentCore Memory, a Bedrock
> Guardrail, structured-output contracts and a human-in-the-loop gate —
> composed entirely in `config.yaml`.

The orchestration is a `delegate` block in YAML. The generic wiring (agent
factory, Gateway client, Memory session manager) is shared with every other use
case in `../_shared/`.

## The pipeline

**UNDERSTAND → DECIDE → EXECUTE → LEARN.** A `delegate` orchestration makes the
coordinator call each specialist as a tool, in order.

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
| `tools.py` | The deterministic steps, as `@tool` functions |
| `schemas.py` | Pydantic structured-output contracts |
| `taxonomy.py` | The 8 HESTA intents |
| `knowledge.py` | HESTA house style + per-intent snippets |
| `ingestion.yaml` | How inbound emails are cleaned up (footer, banner, web form, member-number patterns) — applied by `../_shared/ingestion.py` |
| `settings.py` | HESTA-only env vars (intent threshold, HITL flag, guardrail sentinel) |
| `sample_emails/*.json` | Payloads to try: contact form, hardship with attachment, personal-advice request |
| `../_shared/` | Agent factory, Gateway MCP client, Memory session manager, generic settings |
| `../main.py` | Shared entrypoint — `USE_CASE` selects this folder (see `SWITCHING_USE_CASES.md`) |

---

## Run it

```bash
sca dev --config app/hesta_agent/config.yaml
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
  -d @app/hesta_agent/sample_emails/hardship_with_attachment.json
```

For the full pipeline, point it at your deployed resources:

```bash
# set AGENTCORE_GATEWAY_URL, MEMORY_CLAIMSAGENTMEMORY_ID, GUARDRAIL_ID in .env.local
export LOCAL_DEV=1
sca dev --config app/hesta_agent/config.yaml
```

## Deploy it

This folder is not deployed on its own. The single `multiusecaseagent` runtime
in `agentcore/agentcore.json` packages all of `app/`; `app/main.py` selects this
use case with `USE_CASE = "hesta_agent"`. See `SWITCHING_USE_CASES.md`, then:

```bash
agentcore deploy --target dev --yes
```

Deploy-time wiring the agent needs:

- a Cognito M2M credential provider named by `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER`
  (`agentcore add credential`),
- `AGENTCORE_GATEWAY_URL` (or `AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL`) pointing at
  the Gateway,
- `MEMORY_CLAIMSAGENTMEMORY_ID` for AgentCore Memory,
- `GUARDRAIL_ID` / `GUARDRAIL_VERSION` for the no-personal-advice guardrail.

---

## How it is built

### Agents → `agents:`

Each of the nine agents is one YAML block: model, description, prompt, tools,
structured-output contract. The intent taxonomy is inlined in
`agents.intent_identifier.system_prompt`. Regenerate it after editing `INTENTS`
in `taxonomy.py`:

```bash
python -c "import sys; sys.path.insert(0,'app'); from hesta_agent import taxonomy; print(taxonomy.render_for_prompt())"
```

### Cost-based model routing → `models:`

Three named models: `fast` for classification, `primary` for summarising and
reviewing, and `guarded` — the Writer's model with the Bedrock Guardrail attached:

```yaml
guarded:
  provider: bedrock
  model_id: ${primary_model}
  params:
    guardrail_id: ${GUARDRAIL_ID:-}
    guardrail_redact_output: true
    guardrail_redact_output_message: "[GUARDRAIL_BLOCKED_ADVICE]"
```

`GUARDRAIL_ID` unset means `BedrockModel` omits `guardrailConfig` entirely, so
the pipeline degrades to app-layer controls (the Writer and Reviewer prompts).

### Orchestration → `orchestrations:`

A `delegate` orchestration plus the coordinator's prompt, which also carries the
exact markdown report structure. `preserve_context: false` on the stateless
specialists means each call starts from the same baseline; `context_manager` uses
`true` because it carries a session manager.

### Deterministic steps → `@tool` functions

| `tools.py` | Used by |
|------------|---------|
| `normalize_email` | coordinator — strips banners/footers/quoted history, extracts member number |
| `route_decision` | coordinator — the human-in-the-loop gate |
| `assess_attachments` | attachment_validator |
| `classify_verification` | identity_profiler — the verification policy |
| `filter_member_cases` | case_status_checker |
| `hesta_knowledge`, `identity_verification_block` | writer |

This keeps the decisions deterministic. `route_decision` is not a judgement call
the model makes — it applies `INTENT_CONFIDENCE_THRESHOLD` and returns fixed
reasons. The agents' prompts tell them to return its verdict unchanged.

### Structured output → `agent_kwargs.structured_output`

`Agent` needs a class and YAML can only carry a name, so the config names one as
`package.module:ClassName` and `../_shared/factory.py` imports it:

```yaml
intent_identifier:
  agent_kwargs:
    structured_output: hesta_agent.schemas:IntentResult
```

strands returns structured output through `Agent.as_tool()` as a `json` content
block, so the coordinator receives validated objects rather than prose.

### AgentCore Memory → `session_manager:`

Declared at the root and opted out of with `session_manager: ~` by every agent
except `context_manager`. `../_shared/memory.py` degrades gracefully: with a
`memory_id` it binds the `claims/{actorId}/facts` (SEMANTIC) and
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

So `agent_kwargs.gateway: true` asks `../_shared/factory.py` to build the client
then, and hands it to the agent as a tool provider. The agent sees the real
Gateway tool list (`policy-lookup___lookup_policy`,
`list-pending-claims___list_pending_claims`, `create-claim___create_claim`,
`human-review___request_human_review`) and calls it itself. The prompts name the
bare tool and tell the agent to match on the suffix, so the `___` namespacing
needs no static map. Determinism is preserved by keeping the *policy* in
`classify_verification` / `filter_member_cases` — the agent fetches, the tool
decides.

`continue_on_error=True` on the client means a Gateway outage logs and yields no
tools rather than failing the invocation.

**If your MCP server takes a static token or no auth**, none of this applies —
declare it in YAML and list it under each agent's `mcp:`. `config.yaml` has the
block commented out at the bottom.

---

## Known limitation

**Memory actor is static.** A YAML-declared session manager is resolved once per
session, so `actor_id` is fixed in `config.yaml` (`hesta-context-manager`).
Session-scoped recall works as usual; cross-email recall is scoped to that actor
rather than to the individual member.
