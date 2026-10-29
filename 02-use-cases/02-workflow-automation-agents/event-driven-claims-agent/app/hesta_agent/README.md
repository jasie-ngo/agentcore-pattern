# HESTA Member-Email Pipeline (blueprints)

> Five blueprint agents, three decision tables, MCP Gateway tools, AgentCore
> Memory, a Bedrock Guardrail and a human-in-the-loop gate — composed in YAML.
> There is no HESTA Python: every agent is an agent blueprint filled in, every
> rule a decision table.

The generic wiring (agent factory, Gateway client, Memory session manager, the
blueprints and the decision engine) is shared with every other use case in
`../_shared/`. See [docs/FRAMEWORK.md](../../docs/FRAMEWORK.md).

## The pipeline

**UNDERSTAND → DECIDE → EXECUTE → LEARN.** A `delegate` orchestration makes the
coordinator call each specialist as a tool, in order.

```
              coordinator  (delegate entry · the only hand-written prompt)
              tools: normalize_email, verify_identity, check_attachments, route_case
              Gateway: lookup_policy, list_member_cases, create_claim, request_human_review
                              │
   ┌──────────────────────────┼──────────────────────────────┐
   │ 1 UNDERSTAND             │ 2 DECIDE                     │ 3 EXECUTE
   │                          │                              │
   ├─ intent_identifier       ├─ lookup_policy ──▶ MCP       ├─ writer ──▶ 🛡 guardrail
   │    AI-001  classifier    │  verify_identity   (table)   │    AI-011  writer
   │                          │    AI-003                    │
   ├─ context_manager ──▶ 🧠  ├─ check_attachments (table)   ├─ reviewer_editor
   │    AI-002  extractor     │    AI-004                    │    AI-012  reviewer
   │                          │                              │
   └─ list_member_cases ▶ MCP ├─ empathy                     └─ hand-off ──▶ MCP
        (status enquiries)    │    AI-005  extractor              create_claim +
                              │                                   request_human_review
                              └─ route_case  (table, collect: every reason)

   🧠 AgentCore Memory     MCP  AgentCore Gateway (streamable HTTP + Cognito M2M)
```

The draft reply is **displayed** for HESTA staff to review and send. Nothing is
ever emailed to the member. Escalation writes a DynamoDB record through the
existing Gateway tools.

## Files

| File | Purpose |
|------|---------|
| `config.yaml` | Models, the 5 blueprint agents + coordinator, guardrail, memory, orchestration. The taxonomy, house style and approved knowledge live in the agents' forms |
| `decisions.yaml` | `verify_identity` (AI-003 policy), `check_attachments` (AI-004), `route_case` (the human-in-the-loop gate) |
| `ingestion.yaml` | How inbound emails are cleaned up (footer, banner, web form, member-number patterns) — applied by `../_shared/ingestion.py` |
| `gateway_tools.yaml` | `uses:` three claims tools; owns `list_member_cases` (blueprint retrieval) |
| `tools.py` | Bindings only: the YAML forms above as agent tools |
| `sample_emails/*.json` | Payloads to try: contact form, hardship with attachment, personal-advice request |
| `../main.py` | Shared entrypoint — `USE_CASE` selects this folder (see `SWITCHING_USE_CASES.md`) |

---

## Run it

```bash
sca dev --config app/hesta_agent/config.yaml
```

With no AWS resources configured it still runs end to end: the Gateway calls
report that the Gateway is unavailable, AgentCore Memory falls back to local file
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
- `GUARDRAIL_ID` / `GUARDRAIL_VERSION` for the no-personal-advice guardrail,
- optionally `INTENT_CONFIDENCE_THRESHOLD` (default 70) and `ENABLE_HITL_RECORD`
  (default true).

---

## How it is built

### Agents → agent blueprints

| Agent | Blueprint | The form holds |
|---|---|---|
| `intent_identifier` (AI-001) | classifier | the 8 intents (signals, examples), `personal_advice_requested` flag, `sender_type` field |
| `context_manager` (AI-002) | extractor | summary, conversation state, outstanding items; carries AgentCore Memory |
| `empathy` (AI-005) | extractor | sentiment, vulnerability flags, complaint, priority, recommended attention |
| `writer` (AI-011) | writer | house style, per-intent knowledge, identity-verification and advice-decline wording, legal sign-off |
| `reviewer_editor` (AI-012) | reviewer | accuracy / tone / compliance checks, guardrail-sentinel rule |

The blueprint writes the system prompt and generates the structured-output
schema; `extra_instructions` carries what is HESTA-specific (e.g. DASP vs
withdrawal). To change the taxonomy or the approved knowledge, edit the form —
there is no Python to keep in sync.

### Deterministic steps → decision tables

| Table | Replaces | What it decides |
|---|---|---|
| `verify_identity` | the identity agent + `classify_verification` | verified (sender email matches an active record, via `same_as`) / partial / unverified |
| `check_attachments` | the attachment agent + `assess_attachments` | the document each intent expects vs the attachment count |
| `route_case` | `route_decision` | `hit_policy: collect` — every reason to escalate (regulated intent, personal advice, identity, unclear or low-confidence intent, multiple intents, vulnerability, priority) |

The threshold in `route_case` is `${INTENT_CONFIDENCE_THRESHOLD:-70}`, filled
from the environment when the table loads. Agents that only wrapped a
deterministic tool are gone: the coordinator calls the tables and the Gateway
tools itself, so a run makes two to four fewer model calls than before.

### Cost-based model routing → `models:`

Three named models: `fast` for classification and signals, `primary` for
summarising, reviewing and the coordinator, and `guarded` — the Writer's model
with the Bedrock Guardrail attached:

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
the pipeline degrades to app-layer controls (the Writer's rules and the Reviewer).

### Orchestration → `orchestrations:`

A `delegate` orchestration plus the coordinator's prompt, which also carries the
exact markdown report structure. `preserve_context: false` on the stateless
specialists means each call starts from the same baseline; `context_manager` uses
`true` because it carries a session manager.

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
then, and hands it to the coordinator as a tool provider. It sees the real
Gateway tool list (`policy-lookup___lookup_policy`,
`list-member-cases___list_member_cases`, `create-claim___create_claim`,
`human-review___request_human_review`, …) and calls it itself. The prompt names
the bare tool and tells it to match on the suffix, so the `___` namespacing
needs no static map. Determinism is preserved by keeping the *policy* in the
decision tables — the coordinator fetches, the table decides.

`continue_on_error=True` on the client means a Gateway outage logs and yields no
tools rather than failing the invocation.

**If your MCP server takes a static token or no auth**, none of this applies —
declare it in YAML and list it under each agent's `mcp:`. `config.yaml` has the
block commented out at the bottom.

---

## Known limitations

**Memory actor is static.** A YAML-declared session manager is resolved once per
session, so `actor_id` is fixed in `config.yaml` (`hesta-context-manager`).
Session-scoped recall works as usual; cross-email recall is scoped to that actor
rather than to the individual member.

**`list_member_cases` filters after the index query.** It queries every pending
case and keeps the member's; fine at pilot volume. At scale, add a
policy-number index to the Claims table.
