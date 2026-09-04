# Event-Driven Claims Agent — AI Coding Assistant Context

> **For humans:** This file provides context for AI coding assistants (Kiro, Cursor, Claude Code, GitHub Copilot). For the human-readable documentation, see [docs/](./docs/README.md), [README.md](./README.md), [docs/tutorial.md](./docs/tutorial.md), or [app/hesta-claimsagent/IMPLEMENTATION_PLAN.md](./app/hesta-claimsagent/IMPLEMENTATION_PLAN.md) for the pilot currently deployed.

This repo contains **two agent applications sharing one event-driven scaffold** (S3 → EventBridge → Trigger Lambda → AgentCore Runtime → MCP Gateway → Lambda tools):

- **`app/claimsagent/`** — the original generic **insurance claims demo**: dual-agent (Claims Processor + Validation Agent) with confidence-based auto-approve/human-review routing. Kept in place as the reference scaffold; **not currently deployed**.
- **`app/hesta-claimsagent/`** — **the actively developed pilot, and the one `agentcore.json` deploys today** (`runtimes[0].name = "hestaclaimsagent"`, `codeLocation = "app/hesta-claimsagent"`). Repurposes the same scaffold into HESTA's (an Australian superannuation fund) member-email agentic operations platform: seven agents understand and prepare a case, a human approves every regulated action, nothing is auto-sent. See [app/hesta-claimsagent/IMPLEMENTATION_PLAN.md](./app/hesta-claimsagent/IMPLEMENTATION_PLAN.md) for the full design (business reframe, intent taxonomy, phased delivery) and `docs/hesta-claims-agent-*.md` at the repo root for the hardening-track spec/decisions log layered on top of it.

> **Important:** AgentCore resources (Runtime, Gateway, Memory, PolicyEngine, OnlineEval) are declared in `agentcore/agentcore.json` and managed by the AgentCore CLI. Supplementary infrastructure (DynamoDB, Lambda tools, SNS, S3, EventBridge) is defined in the TypeScript CDK app at `agentcore/cdk/lib/infra-construct.ts`. The Cognito User Pool (Gateway M2M auth) is managed by `scripts/setup_cognito.sh` (AWS CLI, not CDK). Use `agentcore validate` and `agentcore dev` while iterating; run `agentcore deploy --target dev` to deploy everything together. **This infra layer is shared and unchanged by which app is deployed** — only `agentcore.json`'s `codeLocation` decides whose code the Runtime container runs.

---

## Architecture — `app/hesta-claimsagent` (the deployed pilot)

```
S3 upload (claims-inbox/ — any file is treated as an inbound "email")
  → EventBridge rule
    → Trigger Lambda (lambdas/trigger/handler.py)         — UNCHANGED, not modified by the pilot
      → AgentCore Runtime (Container: app/hesta-claimsagent/)
          normalise → InboundEmail            (ingestion/email_normalizer.py, deterministic)
          UNDERSTAND
            AI-001 Intent Identifier + Attachment Detection  (agents/intent_identifier.py)
            AI-002 Conversation Context Manager               (agents/context_manager.py, uses AgentCore Memory)
          DECIDE
            AI-003 Identity & Profiling                       (agents/identity_profiling.py — REUSES lookup_policy/DynamoDB)
            AI-004 Attachment Validation                      (agents/attachment_validation.py — marker/count-based in the pilot)
            AI-005 Empathy                                    (agents/empathy.py)
            → routing gate (routing.py:decide) — regulated | unverified | low-confidence | missing-attachment → escalate
          EXECUTE
            AI-011 Writer     (agents/writer.py)  — drafts the reply, DISPLAYED as output, never sent
            AI-012 Reviewer & Editor (agents/reviewer_editor.py) — checks the draft
            → human-in-the-loop = a DynamoDB record written via the MCP Gateway (create_claim / request_human_review) — reused, unchanged
          LEARN (cross-cutting, reuse-only)
            AgentCore Memory (SEMANTIC + SUMMARIZATION, actor-keyed on member number) + existing OTEL observability
        → AgentCore Gateway (MCP, Cognito CUSTOM_JWT auth, Cedar policy enforcement)
            → 6 Lambda tool functions (lambdas/<tool>/handler.py) — same tools as claimsagent, reused as-is
```

**Pilot reuse decisions (see `app/hesta-claimsagent/IMPLEMENTATION_PLAN.md` §0 for the full rationale):** identity verification reuses `lookup_policy`/`PoliciesTable` exactly as-is (no new table, no GSI); human-in-the-loop is a DynamoDB record written via the existing MCP Gateway tools, read by staff out-of-band; the Writer's draft is displayed, never auto-sent via SES. **No AWS infrastructure is created or altered by the pilot** — only application code inside `app/hesta-claimsagent`.

**Auth (two separate paths, identical to `claimsagent`):**
- **Inbound to Runtime (Trigger Lambda → Runtime):** AWS_IAM (SigV4). The Trigger Lambda's execution role has `bedrock-agentcore:InvokeAgentRuntime` permission granted by CDK via `runtime.grantInvoke(triggerFn)`. No Cognito credentials needed.
- **Outbound from Runtime to Gateway (Runtime → MCP Gateway):** Cognito M2M JWT via `@requires_access_token(provider_name="cognito-gateway-m2m", auth_flow="M2M")` decorator. Secrets managed by AgentCore Identity vault (registered via `agentcore add credential`). The Gateway validates JWT via CUSTOM_JWT authorizer (Cognito OIDC discovery).

---

## Directory Structure

```
event-driven-claims-agent/
├── AGENTS.md                          # This file
├── CLAUDE.md                          # Claude Code guidance
├── README.md                          # Full project documentation (describes the claimsagent scaffold)
├── deploy.sh                          # One-command deploy (runs CDK; deploys whatever agentcore.json points at — currently hestaclaimsagent)
│
├── app/hesta-claimsagent/             # ★ the pilot agentcore.json deploys today
│   ├── IMPLEMENTATION_PLAN.md         # Authoritative pilot design doc — read this first
│   ├── README.md                      # AgentCore-CLI-generated boilerplate (local dev basics only)
│   ├── Dockerfile                     # Multi-stage, Python 3.12, ARM64, uv-managed
│   ├── main.py                        # Orchestrator: UNDERSTAND → DECIDE → EXECUTE → LEARN
│   ├── config.py                      # ALL env var reads live here — model IDs, thresholds, feature flags,
│   │                                   #   plus the optional DynamoDB canary-routing override (resolve_model_variant)
│   ├── routing.py                     # Deterministic human-in-the-loop gate (decide()) — regulated/unverified/
│   │                                   #   low-confidence/multi-intent/vulnerability/missing-attachment → escalate
│   ├── models.py                      # Pydantic schemas for every agent's structured output
│   ├── ingestion/email_normalizer.py  # Phase-0 InboundEmail normalisation (deterministic, regex — not an LLM call)
│   ├── intents/taxonomy.py            # The 8 HESTA intents (BDBN/BP/COD/DASP/FH/FLS/NOI/RTC) + signals + expected-attachment map
│   ├── knowledge/hesta_snippets.py    # Inline per-intent reply snippets for the Writer (pilot; no Bedrock KB yet)
│   ├── agents/
│   │   ├── base.py                    # Shared Strands Agent construction; cost-based fast/strong model routing;
│   │   │                               #   model_id_override seam for future per-case canary wiring (not yet wired in)
│   │   ├── intent_identifier.py       # AI-001
│   │   ├── context_manager.py         # AI-002 (uses AgentCore Memory session_manager)
│   │   ├── identity_profiling.py      # AI-003 — reuses lookup_policy Gateway tool, verification_level: verified|partial|unverified
│   │   ├── attachment_validation.py   # AI-004 — pilot-scoped to markers/count only (no file bytes yet)
│   │   ├── empathy.py                 # AI-005
│   │   ├── writer.py                  # AI-011 — drafts the reply, yields it as output (never sent)
│   │   ├── reviewer_editor.py         # AI-012
│   │   └── case_status.py             # Reuses list_pending_claims for status/progress enquiries
│   ├── memory/session.py              # AgentCore Memory session manager (SEMANTIC + SUMMARIZATION, actor = member number)
│   └── tools/gateway.py               # MCP Gateway client + call_tool wrapper
│
├── app/claimsagent/                   # Original generic insurance demo — reference scaffold, NOT currently deployed
│   ├── Dockerfile
│   ├── main.py                        # Dual-agent logic: Claims Processor (Sonnet) + Validation Agent (Haiku)
│   ├── config.py
│   ├── routing.py                     # Phase-3 routing: decide_action, resolve_decision/routing
│   ├── memory/session.py
│   ├── tools/structured_output.py
│   └── pyproject.toml
│
├── lambdas/                           # One directory per Gateway tool — shared by both apps
│   ├── schemas/                       # MCP tool schemas (JSON) — matched by CDK
│   ├── trigger/handler.py             # EventBridge → Runtime invocation (SigV4 auth) — unchanged by the pilot
│   ├── create_claim/handler.py        # DDB put on ClaimsTable
│   ├── policy_lookup/handler.py       # DDB get on PoliciesTable (repurposed as the HESTA member lookup)
│   ├── list_pending_claims/handler.py # DDB scan for pending_review claims/cases
│   ├── resolve_claim/handler.py       # DDB update on ClaimsTable + ReviewsTable
│   ├── human_review/handler.py        # DDB put on ReviewsTable + SNS publish
│   └── notification/handler.py        # SES send email — NOT used by the pilot (nothing is auto-sent)
│
├── agentcore/
│   ├── agentcore.json                 # Declarative AgentCore resources. runtimes[0]: name="hestaclaimsagent",
│   │                                   #   codeLocation="app/hesta-claimsagent" — THIS is what actually deploys.
│   ├── aws-targets.json               # Deployment targets (account + region)
│   └── cdk/lib/
│       ├── infra-construct.ts         # Supplementary AWS infra (DynamoDB, S3, SNS, EventBridge, Lambdas — Cognito is script-managed)
│       └── cdk-stack.ts               # Integration: wires infra ARNs + JWT authorizer + runtime env vars
│
├── scripts/
│   ├── deploy.sh                      # Deploy helper
│   ├── destroy.sh                     # Unified teardown (observability → stack → orphans → Cognito → state)
│   ├── cleanup_agentcore.py           # Delete orphaned AgentCore control-plane resources (boto3)
│   ├── setup_cognito.sh               # Create Cognito User Pool via AWS CLI (not CDK)
│   ├── teardown_cognito.sh            # Delete Cognito if script-created
│   ├── enable_observability.py        # Enable Transaction Search + Gateway/Memory deliveries
│   ├── disable_observability.py       # Clean up observability deliveries
│   ├── seed_dynamodb.py               # Populate test insurance policies (for app/claimsagent)
│   ├── seed_hesta_members.py          # ★ Populate test HESTA super-member records (for app/hesta-claimsagent) — same
│   │                                   #   PoliciesTable, repurposed fields. `--list` prints reference with no AWS call.
│   ├── generate_sample_emails.py      # ★ Writes hesta/sample-emails/*.txt, one per seed member, matching each
│   │                                   #   member's intent scenario (contact-form and direct-email shapes)
│   ├── test_invoke.py                 # Direct Runtime invocation (SigV4 auth)
│   ├── test_auth.py                   # Authentication pattern tests (6 scenarios)
│   ├── test_e2e.py                    # Full E2E test suite (5 scenarios, written against claimsagent's insurance flow)
│   ├── test_cedar.py                  # Cedar policy enforcement tests
│   ├── test_local.py                  # Local dev invocation helper
│   └── lint.sh                        # py_compile + ruff checks
│
├── tests/                             # Offline unit tests (unittest, no AWS/Strands/LLM required)
│   ├── test_routing.py                # app/claimsagent's Phase-3 routing logic
│   ├── test_hesta_routing.py          # ★ app/hesta-claimsagent's routing.decide() gate, incl. attachment-status escalation
│   ├── test_hesta_model_routing.py    # ★ config.resolve_model_variant (DynamoDB canary override)
│   ├── test_hesta_agent_base.py       # ★ agents/base.py's model_id_override seam
│   ├── test_structured_output.py      # submit_decision / submit_validation tools (app/claimsagent; skips without Strands SDK)
│   ├── test_lambda_handlers.py        # Lambda tool handlers
│   ├── test_trigger.py                # Trigger Lambda
│   └── sample-claim-email.txt         # Email for E2E test 5 (uses POL-67890)
│
├── hesta/                             # ★ Source material the pilot's design was built from — NOT app code
│   ├── {BDBN,BP,COD,DASP,FH,FLS,NOI,RTC}/  # 23 de-identified real HESTA email samples, folder name = ground-truth intent label
│   ├── Cognizant Emails.xlsx           # Intent taxonomy + monthly volumes (~8,400-8,600 emails/month)
│   ├── Hesta-POC.pptx                  # Proposed capabilities & future-state journey deck
│   ├── test_members.md                 # Reference for seed_hesta_members.py's synthetic records
│   └── sample-emails/                  # Generated by generate_sample_emails.py (gitignored output, not source)
│
├── docs/
│   ├── ARCHITECTURE.md                # System design and data flows (describes the claimsagent scaffold)
│   ├── deployment.md                  # Step-by-step deploy, verify, teardown
│   ├── decisions/                     # Architectural decision records (ADR-0001..0014)
│   └── CONFIGURATION.md               # All config surfaces reference
│
└── (repo root) docs/                  # ★ Hardening-track design docs, layered on top of the pilot above
    ├── hesta-claims-agent-design-decisions-log.md   # 10 hardening decisions, reconciled against the real pilot code
    ├── hesta-claims-agent-spec.md                    # Full technical spec derived from the decisions log
    └── superpowers/plans/2026-09-04-hesta-claims-agent-hardening-phase1.md  # Implemented Phase 1 plan (see below)
```

---

## Build, Test, Deploy

### Deploy everything
```bash
./deploy.sh [region]          # defaults to us-west-2; HESTA-specific scripts default to ap-southeast-2 — pass explicitly
```

This runs: configure target → npm install (CDK) → uv sync (agent) → `agentcore validate` → cdk bootstrap → `agentcore deploy --target dev` → seed DynamoDB → prints test commands. Because `agentcore.json`'s only runtime entry points at `app/hesta-claimsagent`, this deploys the HESTA pilot, not the original insurance demo.

### Manual AgentCore / CDK operations
```bash
agentcore validate                       # validate agentcore.json
agentcore deploy --target dev --yes      # deploy everything (hestaclaimsagent)

# NOTE: agentcore CLI does NOT have a destroy command. Use the destroy script:
./scripts/destroy.sh us-west-2           # full teardown (handles DELETE_FAILED + orphans)

# Or drive the underlying TypeScript CDK directly:
cd agentcore/cdk && npm install && npx cdk diff
```

### Local dev loop (no deploy needed)
```bash
cd app/hesta-claimsagent
source .venv/bin/activate                # created by `uv sync` during deploy/setup
agentcore dev                            # starts a local server on 0.0.0.0:8080

# in a new terminal:
agentcore invoke --dev "What can you do"
```

### Seed test data and generate sample inbound emails (HESTA pilot)
```bash
python3 scripts/seed_hesta_members.py --list                        # print reference, no AWS call
python3 scripts/seed_hesta_members.py --dry-run --region ap-southeast-2
python3 scripts/seed_hesta_members.py --region ap-southeast-2       # writes to the (shared) PoliciesTable

python3 scripts/generate_sample_emails.py                           # writes hesta/sample-emails/*.txt
```

### Invoke the agent (requires deployed stack)
```bash
python3 scripts/test_invoke.py --region us-west-2
python3 scripts/test_invoke.py --region us-west-2 --prompt 'File a claim for POL-12345. $5000 storm damage.'
# Drop in a generated HESTA sample email as --prompt to exercise the real pilot pipeline instead.
```

### Run E2E / auth tests
```bash
python3 scripts/test_e2e.py --region us-west-2            # written against claimsagent's insurance flow
python3 scripts/test_auth.py --region us-west-2           # All 6 auth tests — infra-level, app-agnostic
```

### Run unit tests (offline)
```bash
cd 02-use-cases/02-workflow-automation-agents/event-driven-claims-agent

# IMPORTANT: use the project's own venv, not bare python3 — real dependencies (pydantic, boto3,
# strands) are only installed there. Bare python3 produces false-positive import failures.
./.venv/bin/python3 -m unittest discover -s tests -v
```
As of this writing: 67 tests, all pass. `test_hesta_*.py` files exercise the HESTA pilot's `routing.py`, `config.py`, and `agents/base.py`; `test_routing.py`/`test_structured_output.py`/etc. exercise the original `app/claimsagent` scaffold. **Both apps have identically-named modules** (`config.py`, `routing.py`, `models.py`) — every `tests/test_hesta_*.py` and `tests/test_routing.py` file pops the relevant module(s) from `sys.modules` *before* importing and restores them in a module-level `tearDownModule()`, so the suite is order-independent under `unittest discover`. Follow this exact pattern in any new test file that imports from either app — see `tests/test_hesta_model_routing.py` for the reference implementation, and don't put the restore logic inside `if __name__ == "__main__":` (it never runs under `discover`).

### Lint
```bash
./scripts/lint.sh
# or manually:
find app/ lambdas/ scripts/ -name "*.py" -exec python3 -m py_compile {} \;
```

---

## Key Invariants

1. **`agentcore.json`'s `runtimes[0].codeLocation` decides which app deploys** — currently `app/hesta-claimsagent`. Changing which app is live is a one-line JSON edit, not a code change; check this file first if "the deployed agent" and "the code you're reading" seem to disagree.
2. **The pilot's three reuse decisions are deliberate, not gaps** (`app/hesta-claimsagent/IMPLEMENTATION_PLAN.md` §0): identity reuses `lookup_policy`/`PoliciesTable` as-is (no new GSI/table in the pilot); human-in-the-loop is a DynamoDB record via the MCP Gateway, read out-of-band; the Writer's draft is displayed, never sent via SES. Don't "fix" these by adding new AWS infrastructure without checking the hardening-track docs (`docs/hesta-claims-agent-*.md` at the repo root) first — several of them are explicitly scoped as **post-pilot graduation work**, not bugs in the pilot.
3. **Attachment validation is marker/count-only in the pilot** (`agents/attachment_validation.py`) — no real file bytes are ingested yet, only `[FILENAME]` presence markers. Real content-based classification is a named, not-yet-scoped prerequisite (see `docs/hesta-claims-agent-spec.md` §3.2) — don't assume attachment content is available to any agent.
4. **`routing.py`'s `decide()` gate is deterministic (no LLM)** and takes `(intent_result, profile, empathy, attach)` — it escalates on regulated intent, unverified identity, low/ambiguous intent confidence, multiple confident intents, empathy vulnerability/priority flags, personal-advice requests, **and** a missing expected attachment (`attach.status == "missing"`, added in the Phase 1 hardening pass — see the SDD plan below). If you add a new agent output that should ever force human review, it must be wired into this function explicitly; nothing does so automatically.
5. **`config.py` is the single source of truth for every model-ID read** in `app/hesta-claimsagent` — "ALL env var reads live here" per its own docstring. It layers an optional DynamoDB-backed canary override (`resolve_model_variant(role, seed)`, disabled by default via `MODEL_ROUTING_TABLE=""`) beneath the existing `AGENT_MODEL_ID`/`FAST_MODEL_ID` env vars, keyed by **role** (`"fast"`/`"strong"`), not by individual agent name — `_build_model` in `agents/base.py` doesn't distinguish individual agents today, only fast-vs-strong. Any new model-config mechanism must extend this module, not add a second, competing one.
6. **`agents/base.py`'s `build_agent`/`_build_model` accept an optional `model_id_override`**, unused by any of the 5 call sites today — a stable seam for a future plan to wire in per-case canary selection. Each of the 5 agent modules (`empathy.py`, `context_manager.py`, `writer.py`, `reviewer_editor.py`, `intent_identifier.py`) caches its built `Agent` as a **process-lifetime singleton** — wiring true per-case model selection requires changing that caching, which hasn't been done yet.
7. **Lambda handlers return `json.dumps({...})` directly** — no `{statusCode, body}` envelope. The Gateway strips the HTTP wrapper.
8. **Tool schemas live in `lambdas/schemas/`** — each file maps to a Gateway target in the CDK stack via the `toolSchemaFile` field in `agentcore.json`. Keep schemas in sync with Lambda parameters. Shared by both apps.
9. **Container build, not CodeZip** — runtime deps go in each app's own `pyproject.toml` (managed by `uv`). Each Dockerfile runs `uv sync --frozen`.
10. **`agentcore/agentcore.json` is the source of truth for AgentCore resources** (Runtime, Gateway, Memory, PolicyEngine, OnlineEval). Supplementary AWS infra is in `agentcore/cdk/lib/infra-construct.ts`; `cdk-stack.ts` wires the two together. Don't hand-edit generated CDK output.
11. **Two auth paths:** Inbound to Runtime uses AWS_IAM (SigV4). Outbound from Runtime to Gateway uses Cognito M2M JWT via `@requires_access_token(...)` — secrets live in AgentCore Identity vault, not env vars.
12. **`app/claimsagent` is off-limits for HESTA pilot changes** — it's the reference scaffold. Every hardening-track task (and this plan's global constraints) explicitly excludes touching it.

---

## Environment Variables

### `app/hesta-claimsagent` (set by CDK / read via `config.py`)
| Variable | Purpose |
|---|---|
| `AGENT_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` — strong model (AI-002, AI-011, AI-012) |
| `FAST_MODEL_ID` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` — fast/cheap model (AI-001, AI-004, AI-005) |
| `MODEL_ROUTING_TABLE` | Empty by default (disabled). If set, name of a DynamoDB table read by `resolve_model_variant` for canary overrides — no such table exists yet, this is application-code-only |
| `AGENTCORE_GATEWAY_URL` / `AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL` | MCP Gateway HTTPS endpoint |
| `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER` | `cognito-gateway-m2m` — Identity credential provider name (no secrets) |
| `AGENTCORE_GATEWAY_OAUTH_SCOPES` | `agentcore/invoke` |
| `MEMORY_CLAIMSAGENTMEMORY_ID` / `AGENTCORE_MEMORY_ID` | AgentCore Memory resource ID |
| `MEMORY_RETRIEVAL_TOP_K` | `5` — records recalled per invocation |
| `MEMORY_RETRIEVAL_RELEVANCE` | `0.5` — relevance-score threshold for recall |
| `AUTO_APPROVE_THRESHOLD` | `80` — inherited from the scaffold; not the pilot's main gate (see `INTENT_CONFIDENCE_THRESHOLD`) |
| `INTENT_CONFIDENCE_THRESHOLD` | `70` — below this (or `other_unknown`), a case escalates to human triage |
| `ENABLE_HITL_RECORD` | `true` — when true, escalated cases write a DynamoDB record via the MCP Gateway (the human hand-off) |
| `GUARDRAIL_ID` / `GUARDRAIL_VERSION` | Bedrock Guardrail (no personal financial advice), attached to the Writer's model |
| `AGENT_OBSERVABILITY_ENABLED` | `true` — enables OTEL instrumentation |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `true` — captures LLM messages in traces |

### Lambda functions (set by CDK, shared by both apps)
| Variable | Lambda(s) | Value |
|---|---|---|
| `CLAIMS_TABLE` | create_claim, list_pending, resolve_claim | `ClaimsAgent-dev-Claims` |
| `POLICIES_TABLE` | policy_lookup | `ClaimsAgent-dev-Policies` (repurposed as the HESTA member table) |
| `REVIEWS_TABLE` | human_review, resolve_claim | `ClaimsAgent-dev-Reviews` |
| `REVIEW_SNS_TOPIC_ARN` | human_review | SNS topic ARN |
| `SENDER_EMAIL` | notification | SES verified sender — unused by the pilot |

### Trigger Lambda (set by CDK)
| Variable | Purpose |
|---|---|
| `AGENTCORE_RUNTIME_ARN` | Runtime ARN for SigV4-signed HTTPS invocation |

---

## Test Data (seeded by `seed_hesta_members.py`, `--list` for the full current set)

Same `PoliciesTable` as the original insurance demo (schema unchanged, fields repurposed): `policy_number` → HESTA member number, `holder_name`/`email` → member identity fields checked by AI-003, `policy_type` → account/product type, `status` → `active`/`inactive`/`closed`. Each seeded record is tagged with a `test_scenario` matching one of the 8 intents (BDBN/BP/COD/DASP/FH/FLS/NOI/RTC). Run `python3 scripts/seed_hesta_members.py --list` for the current member list without touching AWS — don't hand-maintain a copy of it here, it changes as scenarios are added.

For the original insurance demo's fixed test policies (`POL-12345`, `POL-67890`, `POL-11111`, `POL-99999`), see `scripts/seed_dynamodb.py`.

---

## Cedar Policies

Two policies (in `agentcore/agentcore.json` under `policyEngines`) enforce authorization at the Gateway, shared by both apps:
- **AllowAllTools** — `permit(principal, action, resource is AgentCore::Gateway)`
- **BlockExcessiveClaims** — `forbid` when `context.toolName == "create-claim"` and `context.input.estimated_amount >= 100000`

Both use `IGNORE_ALL_FINDINGS` validation mode (required for the permit-all policy).

---

## Hardening track (design → articulate → assess → build)

The HESTA pilot above is being hardened toward production in a separate, tracked effort — read these in order if picking up that work:

1. `docs/hesta-claims-agent-design-decisions-log.md` (repo root) — 10 decisions (identity verification, attachment verification, resume-after-approval, Glue vs. Lambda, batch evaluation, DevOps gate, memory, observability, model configuration, multi-client), reconciled against the real pilot code above.
2. `docs/hesta-claims-agent-spec.md` (repo root) — the technical spec derived from that log.
3. `docs/superpowers/plans/2026-09-04-hesta-claims-agent-hardening-phase1.md` — **Phase 1, implemented**: the routing-gate attachment-status fix (Key Invariant 4 above) and the `config.py`/`agents/base.py` model-routing seam (Key Invariants 5–6 above). Later phases (batch-evaluation/DevOps gate build-out, identity post-pilot graduation, AgentCore Memory correction-loop wiring, per-case canary attribution) are named but not yet planned or built.
