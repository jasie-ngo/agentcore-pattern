# Multi-use-case agent framework — architecture and logic

This project is a small framework for **event-driven agents** on Amazon Bedrock
AgentCore. A use case is a folder of configuration plus — only where a real
business rule needs it — a little domain code. The framework supplies everything
else: the front door, the Gateway tools, the agent roles, the decision engine,
the wiring and the deployment.

Three use cases run on the same, unchanged core:

| Use case | Domain | How it is built |
|---|---|---|
| `hesta_agent` | super fund member email (HESTA) | **blueprints only** — 5 blueprint agents, 3 decision tables, 1 blueprint Gateway tool |
| `claims_agent` | insurance claims (fictional SecureGuard) | blueprint agents + a decision table; one hand-written Phase-3 executor; owns the shared tools |
| `card_dispute_agent` | card disputes (fictional Harbour Bank) | **blueprints only** — no prompts from scratch, no schemas, no Lambda or decision code |

In every use case the only hand-written prompt is the coordinator's — the flow
itself (which step runs when, what is passed on, the report layout).

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
 │   agent roles — _shared/agent_blueprints.py (filled in by YAML) │
 │   decisions — decision tables (YAML) or app/<use_case>/tools.py │
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

There are four layers, and each one has a "fill in a form" part and a "write
code" part:

| Layer | Form (no code) | Code (only for business rules) |
|---|---|---|
| **Front door** — clean up inbound messages | `app/<use_case>/ingestion.yaml` | — (the normaliser is generic) |
| **Agents** — understand, assess, review, draft | `app/<use_case>/config.yaml` — agent blueprints filled in (classifier, extractor, assessor, reviewer, writer), models, orchestration | a hand-written agent prompt, when no blueprint fits |
| **Decisions** — deterministic business rules | `app/<use_case>/decisions.yaml` (decision table) | `app/<use_case>/tools.py` — `@tool` functions for rules a table cannot express |
| **Gateway tools** — read and write data | `app/<use_case>/gateway_tools.yaml` (blueprint tools) | `lambdas/<use_case>/<tool>/` — custom tools |

---

## 2. A use case is a folder

```text
app/<use_case>/
├── config.yaml          agents, prompts, models, memory, orchestration (strands-compose)
├── ingestion.yaml       how inbound messages are cleaned up
├── gateway_tools.yaml   the Gateway tools this use case owns (or uses)
├── decisions.yaml       decision table(s) — deterministic business rules
├── seed_data.yaml       test data for the tables this use case creates
├── tools.py             binds decision tables / ingestion as tools; @tool functions for complex rules
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

List mode — every record matching a value, via an index. The value is fixed
(`value:`) or taken from the caller (`from_input: true`):

```yaml
  filter: { index: status-index, key: status, value: pending_review }
  result_key: claims

  # or: this customer's 25 most recent transactions (sorted by the index's sort key)
  filter: { index: customer-index, key: customer_id, from_input: true, limit: 25, newest_first: true }
  result_key: transactions

  # or: pending cases, narrowed to the caller's member number (a DynamoDB filter
  # after the index query; each `where` field becomes a required argument)
  filter: { index: status-index, key: status, value: pending_review, where: { policy_number: "Member number" } }
  result_key: cases
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
today: it reuses `lookup_policy`, `create_claim` and `request_human_review`, with
HESTA members stored in the Policies table, and owns `list_member_cases`.

### Which tools are which today

| Tool | Owner | Kind |
|---|---|---|
| `lookup_policy` | claims | blueprint `retrieval` |
| `list_pending_claims` | claims | blueprint `retrieval` (list mode) |
| `create_claim` | claims | blueprint `record_write` (find-or-create) |
| `request_human_review` | claims | blueprint `record_write` (derived priority + SNS) |
| `send_notification` | claims | custom |
| `resolve_claim` | claims | custom |
| `lookup_customer` | card_dispute | blueprint `retrieval` (by id, or by email via an index) |
| `list_customer_transactions` | card_dispute | blueprint `retrieval` (list mode, `from_input`) |
| `create_dispute` | card_dispute | blueprint `record_write` (find-or-create on customer + transaction) |
| `request_dispute_review` | card_dispute | blueprint `record_write` (derived priority) |
| `list_member_cases` | hesta | blueprint `retrieval` (list mode, `where` on the caller's member number) |

Test data for a use case's own tables goes in `app/<use_case>/seed_data.yaml`
and is loaded with
`uv run --project app python scripts/seed_tables.py <use_case> --region <region>`
(`--dry-run` shows the plan without AWS calls).

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

## 6. Inside the Runtime: agent blueprints and decision tables

* `app/main.py` loads `app/<USE_CASE>/config.yaml` with strands-compose.
* AgentCore Memory comes from `app/_shared/memory.py`, which falls back to local
  sessions when no memory id is configured.
* Each coordinator prompt accepts the envelope; with raw text it normalises first.

### Agent blueprints — common roles, filled in per use case

Pipelines reuse the same few agent **roles**; only the **content** changes. An
agent blueprint (`app/_shared/agent_blueprints.py`) writes the system prompt and
generates the structured-output schema from the YAML form:

| Blueprint | Role | The use case fills in | Output (generated schema) |
|---|---|---|---|
| `classifier` | what is this about? | `labels` (id, description, signals, examples), `flags`, `fields`, `fallback` | labels with confidence, `primary_id`, `needs_triage`, one boolean per flag, each field |
| `extractor` | pull fields out of text | `fields` (type, description, enum) | each field (or null), `missing`, `notes` |
| `assessor` | gather evidence with tools, choose an outcome | `outcomes`, `criteria`, `facts`, `fallback_outcome` | `outcome`, `confidence`, `reasons`, each fact |
| `reviewer` | independent second check | `checks` (name → what must hold) | each check, `approved`, `confidence`, `issues` |
| `writer` | draft a reply | `style`, `knowledge`, `rules`, `signoff` | `subject`, `body`, `assumptions` |

Every blueprint also takes `context` (who the agent works for), `task`,
`extra_instructions` and `gateway: true`. Example from the bank use case:

```yaml
intent_classifier:
  type: ../_shared/agent_blueprints.py:classifier
  model: fast
  agent_kwargs:
    context: ${context}
    task: "Classify why the customer is contacting the bank."
    labels:
      - { id: card_dispute, description: "disputes a card transaction", signals: ["don't recognise"] }
      - { id: fee_query, description: "a question about a fee" }
    flags:
      fraud_suspected: "the customer reports several unknown charges"
```

All three use cases build every specialist this way. How the earlier
hand-written agents map onto blueprints:

| Agent | Blueprint | What the form holds |
|---|---|---|
| HESTA `intent_identifier` | classifier | the 8-intent taxonomy, the personal-advice flag, `sender_type` field |
| HESTA `context_manager`, `empathy` | extractor | summary/state/outstanding; sentiment/vulnerability/priority |
| HESTA `writer` | writer | house style, per-intent knowledge, identity and advice-decline wording, legal sign-off |
| HESTA `reviewer_editor`, claims `claims_validator` | reviewer | accuracy/tone/compliance; decision/amount/description/category checks |
| claims `claims_processor` | assessor | ACCEPT/REJECT criteria, claim facts, REJECT fallback |

Agents that only wrapped a deterministic tool (HESTA's identity, attachment,
case-status and hand-off agents) became decision tables or direct Gateway calls
by the coordinator — the same result for fewer model calls.

When no role fits, or a prompt needs domain tuning beyond `extra_instructions`,
write the agent by hand with `type: ../_shared/factory.py:agent` and a
`system_prompt:` (and `agent_kwargs.structured_output: <module>:<Class>` for a
schema). The coordinators are written this way.

### Decision tables — deterministic rules as a form

LLMs read and write; **decisions stay deterministic.** A decision table
(`app/<use_case>/decisions.yaml`, evaluated by `app/_shared/decision_table.py`)
is an ordered list of rules: the first rule whose conditions all hold decides,
otherwise `default` does. No model is involved, and the business can review the
rules without reading code:

```yaml
name: decide_dispute
description: Decide what happens to a card dispute.
inputs:
  amount:        { type: number }
  fraud_signals: { type: boolean }
  assessment:    { type: string }
rules:
  - id: fraud_pattern
    when: { fraud_signals: true }
    then: { outcome: block_card_and_escalate, escalate: true, reason: "Fraud pattern found." }
  - id: small_valid_dispute
    when: { assessment: valid_dispute, amount: { lt: 100 } }
    then: { outcome: provisional_credit, escalate: false, reason: "Small, valid dispute." }
default: { outcome: human_review, escalate: true, reason: "No rule matched." }
```

Conditions: a plain value means "equals"; otherwise `eq, ne, lt, lte, gt, gte,
in, not_in, is_empty, same_as`. `same_as: <input>` compares two inputs (text
trimmed and case-insensitive — e.g. sender email vs the email on record). A
missing input fails every condition except `is_empty: true`. The result names
the rule that decided (`rule_id`).

`hit_policy: collect` applies **every** matching rule instead of the first — for
checklists like HESTA's `route_case` ("escalate if ANY of these holds, and list
every reason"). The result adds `matches` and `rule_ids`; no match gives the
default. `${VAR:-default}` in the file is filled from the environment when the
table is loaded, so thresholds stay tunable per deployment
(`AUTO_APPROVE_THRESHOLD`, `INTENT_CONFIDENCE_THRESHOLD`). One file may hold
several tables under `tables:`.

A use case turns its forms into agent tools in `tools.py` — bindings only:

```python
from _shared.local_tools import decision_table_tool, ingestion_tool
normalize_email = ingestion_tool(HERE / "ingestion.yaml", use_case="card_dispute_agent")
decide_dispute = decision_table_tool(HERE / "decisions.yaml")
```

Code that applies a table without an agent in between uses
`load_table(path, name)` and `decision_table.evaluate()` — claims'
`execute_claim_decision` does, then runs the Phase 3 Gateway calls. That ordered
sequence of side effects (create, escalate, notify; stop on a re-delivery) is
the one thing no form expresses, so it stays a Python `@tool`.

### Agent wiring

Agents are built per session by `app/_shared/factory.py`, which adds what YAML
cannot express: structured output (generated by a blueprint, or
`agent_kwargs.structured_output: <module>:<Class>` for a hand-written agent) and the Gateway
client (`agent_kwargs.gateway: true`, with a Cognito M2M token from AgentCore
Identity fetched on the request thread).

---

## 7. How to…

### Add a lookup or a record write
Add an entry to `app/<use_case>/gateway_tools.yaml`, rebuild, mention the tool
in the relevant agent's prompt, deploy. No Lambda code.

### Add an agent
Pick the blueprint for the role (§6) and fill in its form in `config.yaml`. Write
a hand-written agent only when no blueprint fits.

### Change a business rule
Edit `app/<use_case>/decisions.yaml` (or the `@tool` function, for rules a table
cannot express). No rebuild needed — it ships with the Runtime.

### Add a custom tool
Create `lambdas/<use_case>/<tool>/handler.py` and `schema.json`, add a `custom:`
entry to `gateway_tools.yaml`, rebuild, deploy.

### Change how inbound messages are cleaned up
Edit `app/<use_case>/ingestion.yaml`, rebuild, deploy (the Trigger is rebundled).

### Add a use case
1. Create `app/<new_use_case>/` with `__init__.py`, `config.yaml`,
   `ingestion.yaml`, `gateway_tools.yaml`, and as needed `decisions.yaml`,
   `seed_data.yaml`, `tools.py`. Start from `app/card_dispute_agent/` — it is
   built from blueprints only.
2. Python files that import siblings put `app/` on `sys.path` first (see the top
   of `claims_agent/tools.py`) and import as `from <new_use_case>.<module> import …`.
3. Rebuild (`scripts/build_tools.py`), run the tests, try it locally with
   `sca dev --config app/<new_use_case>/config.yaml`.
4. Point `USE_CASE` at it and deploy, then seed its tables with
   `scripts/seed_tables.py <new_use_case>`. Upload test files to
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
* **Generic prompts are a starting point.** Agent blueprints give a correct,
  consistent prompt from the form; a domain that needs tuned wording uses
  `extra_instructions` or a hand-written agent. Only the decision tables are
  unit-tested against the scenarios — the agents' judgement needs a model run.

## File map

| Path | What it is |
|---|---|
| `app/main.py` | Runtime entrypoint; `USE_CASE` selects the use case |
| `app/_shared/ingestion.py` | Generic normaliser (stdlib only; also bundled into the Trigger) |
| `app/_shared/agent_blueprints.py` | Agent blueprints: classifier, extractor, assessor, reviewer, writer |
| `app/_shared/decision_table.py`, `local_tools.py` | Decision-table engine; builders that turn forms into agent tools |
| `app/_shared/factory.py`, `gateway.py`, `memory.py`, `settings.py` | Agent wiring shared by every use case |
| `app/<use_case>/` | A use case (see §2) |
| `lambdas/blueprints/retrieval/`, `record_write/` | Blueprint tool code |
| `lambdas/<use_case>/<tool>/` | Custom tool code + schema |
| `lambdas/trigger/` | Front door |
| `scripts/build_tools.py` | Forms → schemas, Gateway targets, manifest |
| `scripts/seed_tables.py` | Loads a use case's `seed_data.yaml` into the tables it created |
| `agentcore/generated/` | Build output — never edit by hand |
| `agentcore/cdk/lib/infra-construct.ts` | Shared tables, tool Lambdas from the manifest, Trigger |
| `tests/test_ingestion.py`, `test_lambda_handlers.py`, `test_build_tools.py`, `test_trigger.py` | Framework tests (run against the real forms) |
| `tests/test_agent_blueprints.py`, `test_decision_table.py` | Agent blueprints, tool builders, every use case's config, every bank decision scenario |
| `tests/test_use_case_decisions.py` | Claims routing and HESTA identity / attachment / routing tables |
