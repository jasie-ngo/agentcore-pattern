# Event-Driven Claims Agent — AI Coding Assistant Context

> **For humans:** This file provides context for AI coding assistants (Kiro, Cursor, Claude Code, GitHub Copilot). For the human-readable documentation, see [docs/](./docs/README.md), [README.md](./README.md), [docs/tutorial.md](./docs/tutorial.md), or [app/hesta-claimsagent/IMPLEMENTATION_PLAN.md](./app/hesta-claimsagent/IMPLEMENTATION_PLAN.md) for the pilot currently deployed.

This repo contains **three agent applications sharing one event-driven scaffold** (the base `agentcore.json` deploys `app/04_hesta_member_email/`; the other two are legacy) (S3 → EventBridge → Trigger Lambda → AgentCore Runtime → MCP Gateway → Lambda tools):

- **`app/claimsagent/`** — the original generic **insurance claims demo**: dual-agent (Claims Processor + Validation Agent) with confidence-based auto-approve/human-review routing. Kept in place as the reference scaffold; **not currently deployed**.
- **`app/hesta-claimsagent/`** — **the original hand-written pilot. Legacy: `agentcore.json` no longer deploys it** (it pointed here until 2026-10-25; a stack deployed before then keeps running it until redeployed). Repurposes the same scaffold into HESTA's (an Australian superannuation fund) member-email agentic operations platform: seven agents understand and prepare a case, a human approves every regulated action, nothing is auto-sent. See [app/hesta-claimsagent/IMPLEMENTATION_PLAN.md](./app/hesta-claimsagent/IMPLEMENTATION_PLAN.md) for the full design (business reframe, intent taxonomy, phased delivery) and `docs/hesta-claims-agent-*.md` at the repo root for the hardening-track spec/decisions log layered on top of it.

- **`app/04_hesta_member_email/`** — the same HESTA pilot rebuilt **YAML-first** on `strands-compose`: agents, prompts, models and delegation are in `config.yaml`, with a small `hesta/` package (tools, factory, gateway, memory). **The core, and what the base `agentcore.json` deploys** (`runtimes[0].name = "memberemailagent"`, `codeLocation = "app/04_hesta_member_email"`; it has a Dockerfile and `uv.lock`). It is where the optional Cedar rule-pack handler is wired in (see Key Invariant 13).

> **Important:** AgentCore resources (Runtime, Gateway, Memory, PolicyEngine, OnlineEval) are declared in `agentcore/agentcore.json` and managed by the AgentCore CLI. Supplementary infrastructure (DynamoDB, Lambda tools, SNS, S3, EventBridge) is defined in the TypeScript CDK app at `agentcore/cdk/lib/infra-construct.ts`. The Cognito User Pool (Gateway M2M auth) is managed by `scripts/setup_cognito.sh` (AWS CLI, not CDK). Use `agentcore validate` and `agentcore dev` while iterating; run `agentcore deploy --target dev` to deploy everything together. **This infra layer is shared and unchanged by which app is deployed** — only `agentcore.json`'s `codeLocation` decides whose code the Runtime container runs.

---

## Architecture — `app/hesta-claimsagent` (the legacy hand-written pilot)

> The YAML port has the same shape (UNDERSTAND, DECIDE, EXECUTE, LEARN), composed in `config.yaml` with identity checked before memory. This section describes the legacy app.

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
├── app/hesta-claimsagent/             # Legacy: the original hand-written pilot (no longer deployed by agentcore.json)
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
├── app/04_hesta_member_email/         # ★ THE CORE, YAML-first (strands-compose). What the base agentcore.json deploys. Has a Dockerfile and uv.lock.
│   ├── config.yaml                    # Agents, prompts, models, delegation. `cedar_pack: ${CEDAR_PACK_DIR:-}` on human_handoff (off by default)
│   ├── main.py                        # create_app(config.yaml) — AgentCore Runtime entrypoint
│   ├── core/                          # ★ CLIENT-NEUTRAL core, ships in every container: cedar_pack.py, gherkin.py, factory.py,
│   │                                  #   gateway.py, memory.py, settings.py. Must name no client (a test enforces it)
│   └── hesta/                         # HESTA's client package: factory.py (thin binding), tools.py, schemas.py, taxonomy.py,
│                                      #   knowledge.py, ingestion.py, settings.py
│
├── rules/                             # ★ Rule-pack tooling entry points and the feature files
│   ├── cedar_pack.py                  # command-line launcher for app/04_hesta_member_email/core/cedar_pack.py (`compile|check|load`)
│   ├── run_feature.py                 # conformance runner (routing feature file vs both implementations); still holds HESTA's routing steps
│   ├── hesta_routing_gate.feature     # 57 scenarios; the `@drift` one is a known disagreement between the two apps
│   └── packs/                         # hesta-tool-boundary, health-insurer-illustrative (authored here; NOT a real insurer's rules)
├── clients/                           # ★ Per-client plug-in area (copy _template/): sources/ (gitignored) and pack/
├── demo/                              # ★ Offline narrated demo, anonymising filter, hash-locked env, run sheet, transcript
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
│   │                                   #   codeLocation="app/04_hesta_member_email" — THIS is what the base spec deploys.
│   ├── aws-targets.json               # Deployment targets (account + region)
│   └── cdk/lib/
│       ├── infra-construct.ts         # Supplementary AWS infra (DynamoDB, S3, SNS, EventBridge, Lambdas — Cognito is script-managed)
│       └── cdk-stack.ts               # Integration: wires infra ARNs + JWT authorizer + runtime env vars
│
├── scripts/
│   ├── deploy.sh                      # Deploy helper
│   ├── destroy.sh                     # Unified teardown (observability → stack → orphans → Cognito → state)
│   ├── cleanup_agentcore.py           # Delete the stack and, for the original deployment (or with --sweep-orphans), orphaned AgentCore resources (boto3)
│   ├── clientctx.py                   # ★ Which deployment is this? Resolves --client / AGENTCORE_CLIENT to the stack, credential, Cognito and table names
│   ├── lib/ctx.sh                     # ★ Shared bash: --client, --project-dir, --target, --plan, --sweep-orphans, and `ctx_load`
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
│   ├── test_cedar_pack_vocabulary.py  # ★ rule-pack compiler/loader; a synthetic second vocabulary runs unchanged
│   ├── test_hesta_cedar_wiring.py     # ★ factory wiring, incl. a real Strands agent loop with a scripted model
│   ├── test_demo_anonymise.py         # ★ the demo's display filter, plus one whole-demo run that checks no client name leaks
│   ├── test_cedar_pack_agent_scope.py # ★ per-agent rules (`for the agent`), incl. two real agents sharing one pack
│   ├── test_hesta_memory_guard.py     # ★ YAML-port memory is opened only for a verified member (incl. the attack case)
│   ├── test_core_is_client_neutral.py # ★ core/ names no client, imports no client package, and works with only the app dir
│   ├── test_hesta_profile_gates.py    # ★ strict/presale profile, sender-authentication cap, COD and RTC rules (ADR-0015)
│   ├── test_model_pin.py              # ★ the default models are the pinned ones; changing them is a deliberate edit
│   ├── test_deployable_core.py        # ★ the base spec deploys the YAML port; Dockerfile, lockfile and pinned models agree
│   ├── test_infra_client_mode.py      # ★ CDK names, per-client key, tags and wording, legacy vs client mode (needs Node)
│   ├── test_assemble_client.py        # ★ the assembler; two assembled clients share no physical name (integration part needs Node)
│   ├── test_client_scripts.py         # ★ the scripts act on one deployment: original names unchanged, `--plan` makes no AWS call, wrong project refused
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

This runs: configure target → npm install (CDK) → uv sync (agent) → `agentcore validate` → cdk bootstrap → `agentcore deploy --target dev` → seed DynamoDB → prints test commands. Because `agentcore.json`'s only runtime entry points at `app/04_hesta_member_email`, this deploys the YAML port. **This is the original single-client flow with the original names; for a client use `scripts/assemble_client.py` (Key Invariant 20).**

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
As of this writing (2026-10-30): 396 Python tests (plus 22 TypeScript tests via `npm test` in `agentcore/cdk`), all pass (run under `unittest discover` with `strands-agents[cedar]`, `strands-compose` and `bedrock-agentcore` installed; the new rule-pack tests skip with a reason without them). `test_hesta_*.py` files exercise the HESTA pilot's `routing.py`, `config.py`, and `agents/base.py`; `test_routing.py`/`test_structured_output.py`/etc. exercise the original `app/claimsagent` scaffold. **Both apps have identically-named modules** (`config.py`, `routing.py`, `models.py`) — every `tests/test_hesta_*.py` and `tests/test_routing.py` file pops the relevant module(s) from `sys.modules` *before* importing and restores them in a module-level `tearDownModule()`, so the suite is order-independent under `unittest discover`. Follow this exact pattern in any new test file that imports from either app — see `tests/test_hesta_model_routing.py` for the reference implementation, and don't put the restore logic inside `if __name__ == "__main__":` (it never runs under `discover`).

### Rule packs and the demo
```bash
# from this directory
python3 rules/cedar_pack.py compile clients/<name>/pack   # also: check, load
python3 rules/run_feature.py rules/hesta_routing_gate.feature
bash demo/setup.sh && demo/.venv/bin/python demo/run_demo.py --fast
```
`cedar_pack.py` needs PyYAML (and `strands-agents[cedar]` for `check`/`load`). `demo/requirements.lock` is hash-locked and tested on Python 3.14 only.

### Lint
```bash
./scripts/lint.sh
# or manually:
find app/ lambdas/ scripts/ -name "*.py" -exec python3 -m py_compile {} \;
```

---

## Key Invariants

1. **`agentcore.json`'s `runtimes[0].codeLocation` decides which app deploys** — currently `app/04_hesta_member_email` (the YAML port; it was `app/hesta-claimsagent` until 2026-10-25). Changing which app is live is a one-line JSON edit, not a code change; check this file first if "the deployed agent" and "the code you're reading" seem to disagree.
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
13. **The rule-pack Cedar handler is wired into `app/04_hesta_member_email`, which the base spec now deploys, but it is off unless a pack is switched on.** `hesta_agent(cedar_pack=...)` in `hesta/factory.py` (a thin binding over `core/factory.py`) attaches it, switched on by `CEDAR_PACK_DIR` (empty = off). The legacy `app/hesta-claimsagent/` does not use it. Do not describe the pack check as protecting a runtime unless `CEDAR_PACK_DIR` is set and the pack verifies. The Gateway's Cedar policies below are separate and still apply.
14. **Core versus client.** Everything in `app/04_hesta_member_email/core/` must hold no client's name, tool names, arguments or values: those live in a pack's `vocabulary.yaml` and in the client package (`hesta/`). `core/` may import only the standard library, `strands`, `strands_compose`, `bedrock_agentcore`, `mcp`, `yaml` and `cedarpy`, and must work when only the app directory exists; `tests/test_core_is_client_neutral.py` enforces both. A second client is a new package beside `hesta/`, not an edit to `core/`. A client's documents and pack go in `clients/<name>/`, and nothing in `clients/` may be imported by the core. If a client seems to need a core change, record it as a core edit rather than working around it. Known exception: `run_feature.py` still holds the first client's routing-gate steps and adapters (extraction not done).
15. **Packs are external by design** — not packaged with the app. A pack loads only if its hashes match, a recompile equals the policy on disk, an approval is bound to both the feature file and the vocabulary, and today's date is inside its window. Approvals are currently **unsigned JSON** (forgeable) and no real pack is approved; `_approve_fixture` in `cedar_pack.py` is a labelled test fixture, never a real approval. Nothing yet delivers a pack to a running container. Rules can be scoped per agent (`for the agent "X"`): the names are listed in the vocabulary's optional `agents:` section and compile to the principal `Agent::"<name>"`, which is how `hesta/factory.py` builds each agent. An agent that carries a pack is denied every tool not allowed for it, **local tools included**, so list them in the vocabulary (a tool's `action` is the local tool's name). Only `human_handoff` is wired to a pack in `config.yaml`.
16. **Never put a client or prospect name on a demo screen.** Everything the demo prints goes through `demo/anonymise.py` (the client is renamed; a prospect raises an error). The health-insurer pack is illustrative and must keep that label.
17. **Memory in the YAML port is gated on a verified identity; in the legacy app it is not.** `app/04_hesta_member_email` has no session manager. `recall_member_memory` and `remember_member_contact` (`hesta/tools.py`, logic in `core/memory.py`) do nothing unless `classify_verification` has verified that member number for that sender address, and the actor is the member number. `app/hesta-claimsagent/main.py` still keys memory on the number in the email text (or the sender address) and opens it before the identity check, so a sender can recall and write into another member's memory. Known gap, deliberately not fixed there. Do not describe the legacy app's memory as per-member-safe, and do not redeploy it for real member data.
18. **Deployment profile (ADR-0015).** `DEPLOYMENT_PROFILE` is `strict` unless it is exactly `presale` (`core/profile.py`; a typo means strict). Strict caps a sender who has not passed DMARC at `partial`; presale accepts the address match and is for synthetic data only. **Today nothing sets the authentication signal** (the Trigger Lambda forwards only the body and `From:`), so every strict case is capped. The report stamp is a prompt instruction, not code-enforced. Real member data must never go into a presale deployment: that is a rule, not something the code can detect.
19. **COD and RTC take the light path only if the request is not an email change or login restore (COD) and not an instruction to move money (RTC).** Enforced in `route_decision` by two intent flags plus a deliberately broad pattern backstop. A bare imperative ("Transfer my super") relies on the flag. Models are pinned (`tests/test_model_pin.py`).
20. **One project per client (ADR-0015 Q7).** `scripts/assemble_client.py <client>` builds `build/<client>/` with that client's `agentcore.json` (project, memory and credential names; placeholders for Cognito identity, never the base's), the CDK app set to the client in `cdk.json`, and the core plus client package with the client's pack copied in. It refuses to overwrite a directory it did not make, and `--enable-pack` is refused unless the pack verifies. Do not edit `build/` by hand and do not commit it.
21. **Infrastructure names carry the client, and no client means the original names.** `agentcore/cdk/lib/naming.ts` produces every physical name for the stack's own resources; with no client they are exactly the original ones (so an existing deployment is not replaced), with a client each is prefixed and the data stores use a key of the client's own. `tests/test_infra_client_mode.py` and `tests/test_assemble_client.py` prove this from synthesised templates, including that two assembled clients in one account and region share no name. The one change that applies to every mode is the extra `AGENTCORE_MEMORY_ID` variable on the runtime. **The scripts take the client too (2026-10-27).** `deploy.sh`, `destroy.sh`, `scripts/destroy.sh`, `setup_cognito.sh`, `teardown_cognito.sh`, `fix_credential_region.sh`, `cleanup_agentcore.py`, the observability, seed and test scripts accept `--client <slug>` (or `AGENTCORE_CLIENT`) and get every name from `scripts/clientctx.py` (shell scripts through `scripts/lib/ctx.sh`), which reads the assembled project's own `agentcore.json` and refuses a project that belongs to another client. No client means the original names, and they are pinned by `tests/test_client_scripts.py`. `--plan` prints the names and stops before any AWS call. A client's teardown does not sweep leftover AgentCore resources by name unless `--sweep-orphans` is given, and then matches the client's project name as a whole word, never "contains claims" (that loose match is kept for the original deployment only). In client mode seeding never falls back to scanning for any table with `Policies` in its name. **Not done:** the scripts have not been run against AWS (only `--plan` and the offline tests), and the runtime's `bedrock:InvokeModel` policy lists only the Sonnet foundation model (whether Haiku calls pass is unverified without AWS access). `deploy.sh` Step 4 now installs dependencies in the runtime's own `codeLocation` from `agentcore.json`; it was hard-coded to `app/claimsagent`.
22. **The guardrail's wording is a client's file, not code (2026-10-30).** `agentcore/cdk/lib/guardrail-topics.ts` loads and validates `clients/<client>/guardrail/topics.json` (copied by the assembler to `agentcore/guardrail.json`, or named by `-c guardrailTopics=`, `AGENTCORE_GUARDRAIL_TOPICS` or `cdk.json`). Validation uses Bedrock's limits (topics, name, definition 200, five examples of 100, message 500) and reports every problem at once; a named file that is missing or invalid stops the synth and never falls back. With no file: no client gives the ORIGINAL wording byte for byte (proved against a baseline synth), a client gives neutral wording with no industry or organisation in it. **The `[GUARDRAIL_BLOCKED_ADVICE]` sentinel is not configurable**: the Reviewer matches it by value. `clients/_template/guardrail/` has the template and the authoring guide. The description's 200-character limit was not confirmed in AWS docs.
23. **The pipeline is a neutral base plus a client overlay (2026-10-30).** `app/04_hesta_member_email/core/pipeline.base.yaml` (models, pinned defaults, four shared variables; names no client) is merged by `scripts/assemble_client.py` (logic in `scripts/pipeline_merge.py`) with `clients/<client>/pipeline/pipeline.yaml` (package, agents, orchestrations, entry, optional vars and new model aliases). The overlay may not redefine base variables or model aliases, may not use `model_id`, and every `type:`/`tools:` path must be `./<package>/` or `./core/`. It is **refused if it names another client anywhere in its text** (the copy-paste guard). With an overlay the image holds `core/` and that client's package only: other client packages and `sample_emails/` are left out, and `config.yaml` is the generated merge. **With no overlay the build still ships the first client's whole app folder and prints a warning**, so an insurer is not safe to ship until its overlay and package exist. HESTA's own `config.yaml` is still hand-maintained and is not generated from an overlay; `tests/test_assemble_pipeline.py` checks it agrees with the base on everything shared. Not covered: the folder name `app/04_hesta_member_email` and `main.py`'s docstring, which still name the first client.
24. **A second client exists: an ILLUSTRATIVE finance insurer for hardship and claims enquiries (2026-10-30).** `app/04_hesta_member_email/fininsurer/` (package), `clients/fininsurer/pipeline/pipeline.yaml` (overlay), `clients/fininsurer/guardrail/topics.json` (two topics: personal advice, promised outcomes). Made-up wording and rules for a pre-sale demo with synthetic data, not a real insurer's: the insurer's own hardship policy and approved text must replace them. Design points: nine intents (two hardship, four more that only a person decides); `hardship_indicated` is a flag set whatever the intent, backed by message-text patterns in `route_decision`; any hardship, vulnerability, complaint, cancellation instruction, contact-detail change or third-party sender goes to a person, and only routine information requests take the light path; **a policy in arrears or lapsed still verifies** (a hardship customer is often behind on premiums); missing hardship evidence is never a condition of help; hardship contacts ARE remembered like any other (a deliberate choice, the opposite of the recommendation: revisit before any real engagement). `tests/test_fininsurer.py` assembles the client and runs the real strands-compose loader over the result (all 10 agents build). **Core edit made for it:** `core/memory.py` `_MEMBER_NUMBER` now accepts a letter or digit first, then letters, digits, `-` or `_` (was alphanumeric only), because `POL-12345` otherwise never became a memory actor and memory silently never opened. Nothing is rewritten, so no two inputs can share an actor; `john.smith` is still refused. The AWS docs show hyphens and underscores are allowed in an actor id, but the exact pattern text was garbled in the search result. **Demo assets (all synthetic, all tracked):** `clients/fininsurer/pack/` is a Cedar tool-boundary pack for the one agent that carries a pack (`human_handoff`): `create_claim` only as `pending_review` and `escalated`, `request_human_review` allowed, `resolve_claim` and `send_notification` never, everything else denied. It compiles to what is committed, its examples pass against the real handler, its vocabulary matches the Gateway schemas, and it has **no approval**: it is a draft, `--enable-pack` is refused, and a test fails if an approval is ever committed (an approval must come from a person). `clients/fininsurer/demo/` holds 14 policyholders and 2 pending claims (`policyholders.json`), 15 sample emails (`emails/`, one a web form), `scenarios.json` (the expected outcome of each) and a README. `scripts/seed_policyholders.py` is a generic seeder: it needs `--client`, refuses any record not marked `synthetic` or with a non-example email, never touches the shared tables, has an offline `--list` and `--dry-run`, and `--reset` deletes only synthetic records. `tests/test_fininsurer_demo.py` runs every email through the trigger's real parsing, the normaliser, the identity check and the routing gate. **It also records what the message text alone would do with no classifier flag: four of the demo's cases (03, 04, 07, 11) escalate only because the intent identifier sets a flag, which is an LLM judgement the tests do not cover**, and the README says so. Not done: a deploy, any run against AWS, and evaluating the model-driven agents.

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
