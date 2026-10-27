# Multi-use-case agent framework — architecture and logic

This project is a small framework for **event-driven agents** on Amazon Bedrock
AgentCore. A use case (today: insurance **claims** and **HESTA** member email) is
a folder of configuration plus a little domain code. The framework supplies
everything else: the front door, the Gateway tools, the agent wiring and the
deployment.

The rule behind the whole design:

> **Describe it in a form if you can; write code only for real business rules.**

---

## 1. The big picture

```text
        S3 upload: claims-inbox/<use_case>/<file>
                          │
                          ▼
 ┌──────────────── Trigger Lambda  (lambdas/trigger) ───────────────┐
 │ 1. picks the use case from the folder name                      │
 │ 2. normalises the message  ← app/_shared/ingestion.py           │
 │                             + app/<use_case>/ingestion.yaml     │
 │ 3. sends the ENVELOPE to the Runtime as the prompt              │
 └──────────────────────────────┬───────────────────────────────────┘
                                ▼
 ┌──────────── AgentCore Runtime  (container = app/) ───────────────┐
 │ app/main.py  → app/<USE_CASE>/config.yaml  (strands-compose)    │
 │   agents, prompts, models, orchestration — YAML                 │
 │   deterministic decisions — app/<use_case>/tools.py (@tool)     │
 │   agent factory, Gateway client, Memory — app/_shared/          │
 └──────────────────────────────┬───────────────────────────────────┘
                                ▼  MCP + Cognito M2M token
 ┌──────────── AgentCore Gateway  (Cedar policies enforced) ────────┐
 │ one target per tool declared in app/*/gateway_tools.yaml        │
 └──────────────────────────────┬───────────────────────────────────┘
                                ▼
 ┌──────────── Tool Lambdas ────────────────────────────────────────┐
 │ blueprint tools: lambdas/blueprints/{retrieval,record_write}    │
 │   same code, different settings (TABLE_NAME, TOOL_CONFIG)       │
 │ custom tools:    lambdas/<use_case>/<tool>  (hand-written)      │
 └──────────────────────────────┬───────────────────────────────────┘
                                ▼
                     DynamoDB / SNS / SES
```

There are three layers, and each one has a "fill in a form" part and a "write
code" part:

| Layer | Form (no code) | Code (only for business rules) |
|---|---|---|
| **Front door** — clean up inbound messages | `app/<use_case>/ingestion.yaml` | — (the normaliser is generic) |
| **Agents** — understand, decide, draft | `app/<use_case>/config.yaml` (agents, prompts, models, orchestration) | `app/<use_case>/tools.py` — deterministic decisions such as routing gates |
| **Gateway tools** — read and write data | `app/<use_case>/gateway_tools.yaml` (blueprint tools) | `lambdas/<use_case>/<tool>/` — custom tools |

---

## 2. A use case is a folder

```text
app/<use_case>/
├── config.yaml          agents, prompts, models, memory, orchestration (strands-compose)
├── ingestion.yaml       how inbound messages are cleaned up
├── gateway_tools.yaml   the Gateway tools this use case owns (or uses)
├── schemas.py           structured-output contracts (Pydantic)
├── tools.py             deterministic @tool functions — business rules
├── settings.py          use-case-only environment variables
└── __init__.py
lambdas/<use_case>/<tool>/   custom tool Lambdas, if any (handler.py + schema.json)
```

A developer adding or changing a use case touches **only** these folders.
`agentcore/agentcore.json`, the CDK stack and `app/_shared/` are framework code;
the build step updates `agentcore.json` for them (see §5).

Which use case the Runtime serves is one line in `app/main.py`:

```python
USE_CASE = "hesta_agent"  # change to "claims_agent" to switch use case
```

See [SWITCHING_USE_CASES.md](../SWITCHING_USE_CASES.md) for the switch procedure.

---

## 3. Front door: the envelope

Every inbound message is turned into one standard **envelope** before an agent
sees it, whatever channel it came through. The Trigger Lambda does this for
every S3 upload; each use case's `normalize_email` tool does the same for direct
invocations (e.g. `scripts/test_invoke.py`).

```json
{
  "envelope_version": 1,
  "use_case": "hesta_agent",
  "source": "s3://bucket/claims-inbox/hesta_agent/msg.txt",
  "channel": "contact_form",
  "sender_type": "member",
  "from_email": "tom.fletcher@example.com",
  "sender_name": "Tom Fletcher",
  "subject": "",
  "id_raw": "60030001",
  "id_for_lookup": "60030001",
  "form_reason": "Updating my account details",
  "latest_message": "My mobile number has changed…",
  "attachment_count": 0,
  "raw_text": "You've received a new form based mail…"
}
```

### How it works

The code (`app/_shared/ingestion.py`) knows only **generic** email conventions:
quoted-reply markers (`From:… Sent:…`, `On … wrote:`), `[ATTACHMENT …]` markers,
email addresses, bracketed placeholders such as `[MEMBER NUMBER]`. Everything
that is specific to a business comes from that use case's `ingestion.yaml`:

| Setting | Meaning | HESTA example |
|---|---|---|
| `footer_markers` | Text from which everything is a legal footer — dropped | `"Issued by H.E.S.T"` |
| `banner_markers` | Lines starting with these are a security banner — dropped | `"WARNING:"` |
| `form` | A web form: how to detect it, its field labels, which label is the id / sender / message / reason | the hesta.com.au Contact-Us form |
| `id_patterns` | How a customer id looks, tried in order (`regex`, `group`, `uppercase`) | `POL-…`, "member number: …", a 5–9 digit run |
| `sender_types` | Phrases that identify a sender type on non-form emails | solicitor markers → `third_party` |
| `default_sender_type` | Sender type when nothing else matches | `member` |

### Routing to a use case

The Trigger takes the use case from the S3 folder:
`claims-inbox/<use_case>/<file>`. A file directly in `claims-inbox/` (or in an
unknown folder) goes to the default use case — the one `app/main.py` deploys.

### Why the front door matters

* **Sender and subject reach the agent.** The Runtime only passes the payload's
  `prompt` to the agent, so anything sent beside it is lost. The envelope
  carries sender, subject and source *inside* the prompt.
* **No model call is spent on parsing**, and the result is deterministic and
  unit-tested.
* **Every use case gets the same input shape**, so agents and tools can rely on
  `from_email`, `id_for_lookup` and `source` existing.

---

## 4. Gateway tools: blueprints and custom tools

Agents read and write data only through the AgentCore Gateway, so every call
gets Cognito authentication, Cedar policy checks and an audit trail. Each tool
is one Gateway target backed by one Lambda.

### Blueprint tools — fill in a form

A **blueprint** is one tested piece of Lambda code that does a generic job. A
tool built from it is just a form entry; the build gives each tool its own copy
of the blueprint with its own settings.

**How form values reach the code.** The blueprint code never reads YAML. At
build time each entry is turned into settings (environment variables) on its
Lambda; at run time the code reads its settings:

```text
gateway_tools.yaml entry ──build──▶ Lambda settings: TABLE_NAME, TOOL_CONFIG (JSON)
                                  ▶ tool schema the agent sees (from the same entry)
Lambda runs ──reads its settings──▶ looks in the right table, returns the right fields
```

#### `retrieval` — read records

```yaml
- name: lookup_policy                 # tool name the agent sees
  target: policy-lookup               # Gateway target (default: name with - for _)
  description: Look up insurance policy details by policy number
  blueprint: retrieval
  table: policies                     # a key under tables:
  lookup:                             # try each key the caller supplied, in order
    - { key: policy_number, description: "The policy identifier" }
    # - { key: email, index: email-index }   ← query a secondary index instead
  returns: [policy_number, holder_name, email, status]   # only these fields go back
```

List mode — every record matching a fixed value, via an index:

```yaml
  filter: { index: status-index, key: status, value: pending_review }
  result_key: claims
```

Responses: `{"found": true, …fields}`, `{"found": false, "error": "Record not found for …"}`,
or in list mode `{"found": …, "count": n, "<result_key>": [...]}`.

#### `record_write` — create records (optionally find-or-create)

```yaml
- name: create_claim
  description: Create a new insurance claim record.
  blueprint: record_write
  table: claims
  id: { field: claim_id, prefix: CLM, from: [policy_number, source] }
  if_exists: return_existing          # new_record | return_existing | error
  inputs:                             # the only fields accepted from the caller
    policy_number: { type: string, required: true, description: "…" }
    estimated_amount: { type: integer, required: true, description: "…" }
    source: { type: string, description: "…" }
  rules:      { estimated_amount: { min: 0, max: 10000000 } }   # also max_length, one_of
  defaults:   { status: pending_review }        # when the caller leaves it out
  set:        { }                               # always written; caller cannot override
  derived:    { priority: { from: estimated_amount, gte: 50000, then: high, else: medium } }
  timestamps: [created_at]
  notify:     { subject: "Review needed: {claim_id}" }          # optional SNS publish
```

**How the id is made, and why it matters.** With `from:`, the id is a hash of
those fields, so the same inputs always produce the same id. With
`if_exists: return_existing`, a second write with the same inputs returns the
record that is already there (`"result": "existing"`) instead of creating a
duplicate — this is "find or create". If any `from` field is missing, a random
id is used. Without `from:`, every write creates a new record.

For claims this solves a real problem: the Trigger Lambda retries failed
deliveries, so one upload could be processed twice. Because `create_claim` is
keyed on policy number + source, the second run finds the first claim, and
`execute_claim_decision` then skips the review request and the notification.

### Custom tools — write the code

Use a custom tool when the job is a **business rule**, not a generic read or
write: it changes more than one table, decides something, or restricts how a
value may change. Today: `resolve_claim` (updates a claim and closes its reviews
in a second table) and `send_notification` (branded SES email).

```yaml
- name: resolve_claim
  target: resolve-claim
  description: Approve or deny a pending claim as a human reviewer
  custom:
    code: lambdas/claims_agent/resolve_claim            # folder with handler.py
    schema: lambdas/claims_agent/resolve_claim/schema.json
    environment:
      CLAIMS_TABLE:  { table: claims }                  # or { env: VAR, default: … } or { value: … }
      REVIEWS_TABLE: { table: reviews }
    tables: { claims: write, reviews: write }           # permissions
    permissions: [ses_send]                             # extra permissions, when needed
```

Custom Lambdas follow the Gateway convention: return `json.dumps({...})`
directly, with no HTTP envelope.

### Tables

```yaml
tables:
  claims:  { existing: Claims }       # a shared table the framework creates
  members:                            # created for this use case
    partition_key: { name: member_id, type: string }
    indexes:
      - { name: email-index, partition_key: { name: email, type: string } }
```

`existing` refers to the shared tables in `agentcore/cdk/lib/infra-construct.ts`
(`Policies`, `Claims`, `Reviews`). A table without `existing` is created as
`<stack>-<use_case>-<key>`.

### Sharing tools between use cases

Tool names and targets are unique across the whole project — the Gateway is
shared. A use case that relies on another use case's tools lists them under
`uses:` so the dependency is explicit and checked at build time. HESTA does this
today: it reuses the claims tools, with HESTA members stored in the Policies
table.

### Which tools are which today

| Tool | Owner | Kind |
|---|---|---|
| `lookup_policy` | claims | blueprint `retrieval` |
| `list_pending_claims` | claims | blueprint `retrieval` (list mode) |
| `create_claim` | claims | blueprint `record_write` (find-or-create) |
| `request_human_review` | claims | blueprint `record_write` (derived priority + SNS) |
| `send_notification` | claims | custom |
| `resolve_claim` | claims | custom |

---

## 5. The build step

The YAML forms are turned into deployable pieces by **`scripts/build_tools.py`**
(run by `deploy.sh`; run it yourself after editing a form):

```bash
uv run --project app python scripts/build_tools.py          # write generated files
uv run --project app python scripts/build_tools.py --check  # verify only (CI, lint.sh)
```

It reads every `app/<use_case>/gateway_tools.yaml` and `ingestion.yaml`,
validates them, and writes:

| Output | Used by |
|---|---|
| `agentcore/generated/schemas/<tool>.json` | the Gateway — the tool description the agent sees |
| `agentcore/agentcore.json` → Gateway `targets` | AgentCore CLI and CDK — one target per tool |
| `agentcore/generated/manifest.json` | CDK — which Lambdas, tables, settings and permissions to create; the Trigger's ingestion settings |

Validation catches mistakes before AWS does: unknown blueprint or table,
missing fields, duplicate tool names across use cases, a `uses:` entry nobody
declares, a custom tool whose schema doesn't define it, settings too large for
a Lambda.

**Stale builds cannot be deployed.** The manifest records a hash of the forms.
CDK recomputes it at synth and stops with
`… changed since the last tool build — run: uv run --project app python scripts/build_tools.py`
if they differ. `tests/test_build_tools.py` fails the same way, so CI catches a
forgotten rebuild too.

**At synth, CDK** (`infra-construct.ts`) then:

1. creates the tables the forms declare;
2. creates one Lambda per tool — blueprint code or the custom folder — with its
   settings and least-privilege permissions (a `retrieval` tool can only read
   its own table);
3. stages the Trigger Lambda together with `app/_shared/ingestion.py` and every
   use case's ingestion settings (no copies are kept in the repo);
4. patches each Gateway target with its Lambda's real ARN.

---

## 6. Inside the Runtime

* `app/main.py` loads `app/<USE_CASE>/config.yaml` with strands-compose.
* Agents are built per session by `app/_shared/factory.py`, which adds what YAML
  cannot express: structured output (`agent_kwargs.structured_output:
  <use_case>.schemas:<Class>`) and the Gateway client (`agent_kwargs.gateway: true`,
  with a Cognito M2M token from AgentCore Identity fetched on the request thread).
* AgentCore Memory comes from `app/_shared/memory.py`, which falls back to local
  sessions when no memory id is configured.
* Each coordinator prompt accepts the envelope; with raw text it normalises
  first (HESTA calls `normalize_email`; claims reads raw text as-is).
* **Decisions stay deterministic.** LLMs read and write; Python tools decide —
  e.g. HESTA's `route_decision` and `classify_verification`, claims'
  `execute_claim_decision`. Prompts tell agents to pass those results on unchanged.

---

## 7. How to…

### Add a lookup or a record write
Add an entry to `app/<use_case>/gateway_tools.yaml`, rebuild, mention the tool
in the relevant agent's prompt, deploy. No Lambda code.

### Add a custom tool
Create `lambdas/<use_case>/<tool>/handler.py` and `schema.json`, add a `custom:`
entry to `gateway_tools.yaml`, rebuild, deploy.

### Change how inbound messages are cleaned up
Edit `app/<use_case>/ingestion.yaml`, rebuild, deploy (the Trigger is rebundled).

### Add a use case
1. Create `app/<new_use_case>/` with `__init__.py`, `config.yaml`,
   `ingestion.yaml`, `gateway_tools.yaml` and any `schemas.py` / `tools.py`.
   Use `type: ../_shared/factory.py:agent` for agents.
2. Python files that import siblings put `app/` on `sys.path` first (see the top
   of `claims_agent/tools.py`) and import as `from <new_use_case>.<module> import …`.
3. Rebuild (`scripts/build_tools.py`), run the tests, try it locally with
   `sca dev --config app/<new_use_case>/config.yaml`.
4. Point `USE_CASE` at it and deploy. Upload test files to
   `claims-inbox/<new_use_case>/`.

---

## 8. Limits and open decisions

* **One use case per deployed Runtime.** Uploads for a use case that is not the
  deployed one are normalised for that use case but still reach the deployed
  agent. Running several use cases at once means one Runtime per use case (not
  built yet).
* **Shared Gateway.** Every agent sees every tool. Prompts name the tools each
  agent should use; Cedar policies apply per tool name.
* **What makes a record "the same"** (`id.from`) is a business decision per use
  case. Claims uses policy number + source file; confirm with the claims team.
* **Rejected claims are not recorded**, so a re-delivered rejection sends the
  rejection notification again.
* **Blueprints cover one table per tool.** Anything touching several tables, or
  with rules about how values may change, is a custom tool.

## File map

| Path | What it is |
|---|---|
| `app/main.py` | Runtime entrypoint; `USE_CASE` selects the use case |
| `app/_shared/ingestion.py` | Generic normaliser (stdlib only; also bundled into the Trigger) |
| `app/_shared/factory.py`, `gateway.py`, `memory.py`, `settings.py` | Agent wiring shared by every use case |
| `app/<use_case>/` | A use case (see §2) |
| `lambdas/blueprints/retrieval/`, `record_write/` | Blueprint tool code |
| `lambdas/<use_case>/<tool>/` | Custom tool code + schema |
| `lambdas/trigger/` | Front door |
| `scripts/build_tools.py` | Forms → schemas, Gateway targets, manifest |
| `agentcore/generated/` | Build output — never edit by hand |
| `agentcore/cdk/lib/infra-construct.ts` | Shared tables, tool Lambdas from the manifest, Trigger |
| `tests/test_ingestion.py`, `test_lambda_handlers.py`, `test_build_tools.py`, `test_trigger.py` | Framework tests (run against the real forms) |
