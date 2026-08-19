# Framework Plan — "Event-Driven Agent Template" (from the Claims sample)

**Goal:** turn `event-driven-claims-agent` into a reusable, config-driven template that an
internal delivery team clones per client, fills in a client-config, and deploys as an
isolated stack — supporting variable agent counts, trigger types, tools, data models, and
policies with minimal code changes.

**Scope decisions:** flexible architecture · internal delivery team · one isolated deployment per client.

## How to use this in Microsoft Planner
- **Buckets** = Phases (0–5).
- **Task title** = `E#.# · <task name>`.
- **Notes** = the task Description.
- **Checklist** = the Acceptance Criteria (one checklist item each).
- **Labels** = Role(s). **Priority** ≈ derived from phase/effort (Phase 0 and E1.2/E5.7 are highest).
- A ready-to-import **CSV** is provided alongside this file: `event-driven-agent-framework-tasks.csv`.

## Legend
**Roles:** SA Solutions/AI Architect · BE Backend/Agent Eng · AIE AI/Prompt Eng · DevOps Infra/DevOps ·
SEC Security · DE Data Eng · QA QA/Test · TW Tech Writer · DL Delivery Lead/PO
**Effort:** S ≤ 2 days · M ~ 1 week · L 2+ weeks

---

## Phase 0 — Harden the baseline
*Make the sample a trustworthy foundation before templatizing. Most of these are already prototyped in the working tree and mainly need review, merge, and tests.*

### E0.1 · Container build hygiene (`.dockerignore`)
**Description:** Stop the host-built `.venv` (and caches/secrets) from being copied into the runtime image, where it overrides the builder-stage venv and crashes the container with HTTP 424.
**Acceptance criteria:**
- `.dockerignore` excludes `.venv/`, `__pycache__/`, `*.pyc`, `.env*`, `.git/`.
- A fresh image contains only the builder-stage venv; `opentelemetry-instrument` resolves inside the container.
- Deployed runtime starts and passes the `/ping` health check.
**Role:** DevOps · **Effort:** S · **Deps:** —

### E0.2 · Region-safe model IDs
**Description:** Replace `us.*` cross-region inference profiles with region-valid `global.*` (or `au.*`) IDs in every location: `agentcore.json`, `config.py` defaults, and `.env`.
**Acceptance criteria:**
- No `us.*` model IDs remain in the repo.
- Processor and Validator both invoke successfully in the target region.
- Model IDs are documented per supported region.
**Role:** AIE · **Effort:** S · **Deps:** —

### E0.3 · Gateway tool-name namespacing map
**Description:** Phase-3 deterministic tool calls use bare names, but the Gateway exposes `<target>___<tool>`. Provide a resolver/map so direct calls hit the correct tool.
**Acceptance criteria:**
- All Phase-3 tool calls (create claim, notify, human review) succeed.
- Map is derived from or validated against the live Gateway tool list.
- No "Unknown tool" errors in a full run.
**Role:** BE · **Effort:** S · **Deps:** —

### E0.4 · Autonomous-operation prompt
**Description:** Remove the "state your plan and wait for user approval" instruction so the Processor decides autonomously in the event-driven pipeline instead of asking for input.
**Acceptance criteria:**
- Prompt explicitly forbids asking for clarification or waiting for approval.
- Processor reaches an ACCEPT/REJECT on a complete claim with no human input.
**Role:** AIE · **Effort:** S · **Deps:** —

### E0.5 · Forced structured-output decision fallback
**Description:** When the Processor streams prose instead of calling `submit_decision`, force a validated decision via `structured_output_async` so the pipeline never silently defaults to REJECT.
**Acceptance criteria:**
- If `submit_decision` is skipped, a forced structured decision is produced.
- Downstream phases receive the same field shape as the tool result.
- Repeated happy-path runs deterministically reach ACCEPT.
**Role:** BE, AIE · **Effort:** M · **Deps:** E0.4

### E0.6 · Region resolution hardening
**Description:** `config.py` hardcodes `us-west-2` when `AWS_REGION` is unset, breaking local `agentcore dev`. Fall back to `AWS_DEFAULT_REGION` then the boto3 session region.
**Acceptance criteria:**
- Local dev with only an AWS profile set resolves the correct region.
- Memory and Gateway calls target the deploy region without extra env exports.
**Role:** BE · **Effort:** S · **Deps:** —

### E0.7 · Memory identifier sanitization
**Description:** Sanitize external identifiers (e.g., claimant email) before using them as AgentCore Memory `actorId`/`sessionId`, which reject `@`, `.`, and other characters.
**Acceptance criteria:**
- Emails and arbitrary inputs map to IDs matching both the actorId and sessionId regexes.
- Memory `ListEvents`/writes succeed with an email-derived actor.
- Empty/invalid input falls back to a safe default.
**Role:** BE · **Effort:** S · **Deps:** —

### E0.8 · Structured tool-result logging
**Description:** Replace noisy nested-escaped `{'raw': ...}` log blobs with clean, structured one-line logs for tool results.
**Acceptance criteria:**
- Successful claim creation logs a readable one-liner (claim id, status, amount).
- Tool errors log clearly with the trace/correlation id.
**Role:** BE · **Effort:** S · **Deps:** E0.3

### E0.9 · Baseline regression test suite
**Description:** Lock the Phase-0 fixes with automated tests so they can't silently regress.
**Acceptance criteria:**
- Tests cover: image excludes venv, model-ID validity, tool-name resolution, forced decision, region fallback, id sanitization.
- Suite runs in CI and fails on regression.
**Role:** QA · **Effort:** M · **Deps:** E0.1–E0.8

---

## Phase 1 — Architecture & the client-config spec

### E1.1 · Core-vs-domain boundary map
**Description:** Document precisely which components are the reusable engine vs. domain-specific per client — the basis for the config spec.
**Acceptance criteria:**
- Doc classifies every file/resource as core or domain.
- Reviewed and signed off by the team.
**Role:** SA · **Effort:** M · **Deps:** Phase 0

### E1.2 · Client configuration spec  *(flagship)*
**Description:** Design the single declarative source of truth — identity/branding, agents, tools, triggers, data entities, policies, models, memory — that drives a client deployment.
**Acceptance criteria:**
- Documented schema with field descriptions and examples.
- Covers every domain-specific surface identified in E1.1.
- Peer-reviewed; a complete sample client config exists.
**Role:** SA · **Effort:** L · **Deps:** E1.1

### E1.3 · Extension-point interfaces
**Description:** Define stable interfaces for Agent, Tool, TriggerAdapter, DataAdapter, and PolicySet so new behavior plugs in without editing core.
**Acceptance criteria:**
- Interfaces defined with types/docstrings.
- At least one reference implementation validates against each interface.
**Role:** SA, BE · **Effort:** M · **Deps:** E1.2

### E1.4 · Architecture decision records
**Description:** Capture key decisions (config format, orchestration model, tenancy) as ADRs.
**Acceptance criteria:**
- ADRs for config format, orchestration model, and isolation model exist and are approved.
**Role:** SA · **Effort:** S · **Deps:** E1.2

### E1.5 · Config schema + validator
**Description:** Provide a machine-readable schema (JSON Schema/Pydantic) and a validator that fails fast with clear messages on invalid client config.
**Acceptance criteria:**
- Invalid configs are rejected with actionable error messages.
- A valid sample config passes.
- Validation runs in CI and pre-deploy.
**Role:** BE · **Effort:** M · **Deps:** E1.2

---

## Phase 2 — Config-driven domain extraction

### E2.1 · Externalize agent definitions
**Description:** Move prompts, model selection, and tool bindings out of `main.py` into config so agents are defined declaratively.
**Acceptance criteria:**
- Each agent's prompt/model/tools load from config.
- No agent-specific literals remain in `main.py`.
- Claims behavior is unchanged when using the claims config.
**Role:** AIE, BE · **Effort:** L · **Deps:** E1.3

### E2.2 · Config-driven structured-output schemas
**Description:** Make the decision/validation output schemas (`submit_decision`/`submit_validation`) configurable per client.
**Acceptance criteria:**
- Output schema fields are defined in config.
- Runtime validates agent output against the configured schema.
**Role:** BE · **Effort:** M · **Deps:** E2.1

### E2.3 · Config-driven routing rules
**Description:** Externalize thresholds and the decision→action matrix (auto-approve / human review / reject) into config.
**Acceptance criteria:**
- Thresholds and routing matrix are read from config.
- Changing routing requires no code edit; covered by tests.
**Role:** BE · **Effort:** M · **Deps:** E2.1

### E2.4 · Parameterize naming/branding
**Description:** Remove hardcoded `ClaimsAgent`/`claimsagent` names; derive all resource names and labels from config.
**Acceptance criteria:**
- No hardcoded product/domain names in code or infra.
- Two different configs produce non-colliding resource names.
**Role:** BE, DevOps · **Effort:** M · **Deps:** E1.2

### E2.5 · Data-model abstraction
**Description:** Generalize DynamoDB tables/entities and seed data from config instead of the fixed Policies/Claims/Reviews model.
**Acceptance criteria:**
- Entities, keys, and seed data are defined in config.
- CDK provisions tables from config; seeds load successfully.
**Role:** DE · **Effort:** L · **Deps:** E1.3

### E2.6 · Pluggable memory namespaces & actor-id strategy
**Description:** Make memory namespaces and the actor-id derivation (including sanitization) configurable per client.
**Acceptance criteria:**
- Namespaces and actor-id strategy are driven by config.
- Sanitization applied; per-actor isolation verified.
**Role:** DE, BE · **Effort:** M · **Deps:** E2.5, E0.7

### E2.7 · Cedar policy templating
**Description:** Generate Cedar policies (thresholds, allow/deny) from config rather than the hardcoded $100k rule.
**Acceptance criteria:**
- Policies are rendered from config values.
- Policy-block behavior is verified by test.
**Role:** SEC · **Effort:** M · **Deps:** E1.3

### E2.8 · Optional Guardrails integration
**Description:** Add opt-in Bedrock Guardrails wired via config for content/PII controls.
**Acceptance criteria:**
- A Guardrails toggle exists in config; applied when enabled, no-op when disabled.
- Configuration is documented.
**Role:** SEC · **Effort:** M · **Deps:** E2.7

---

## Phase 3 — Flexible architecture

### E3.1 · Configurable orchestration
**Description:** Support single / dual / multi-agent pipelines, with phases and agent order defined in config.
**Acceptance criteria:**
- Pipeline shape (agents, order) is driven by config.
- Both a single-agent and a dual-agent config run successfully.
**Role:** BE, SA · **Effort:** L · **Deps:** E2.1

### E3.2 · Pluggable execution phase
**Description:** Make the post-decision execution/action wiring configurable per pipeline.
**Acceptance criteria:**
- Execution actions are declared in config and dispatched generically.
- The claims execution flow is reproduced via config.
**Role:** BE · **Effort:** M · **Deps:** E3.1

### E3.3 · Trigger adapter interface + generic trigger
**Description:** Abstract ingress behind a TriggerAdapter interface plus a generic trigger Lambda.
**Acceptance criteria:**
- Interface defined; email/S3 reimplemented as an adapter.
- Existing runtime invocation behavior is unchanged.
**Role:** BE, DevOps · **Effort:** M · **Deps:** E1.3

### E3.4 · Additional trigger adapters
**Description:** Implement REST/API, schedule (EventBridge), and queue (SQS) trigger adapters.
**Acceptance criteria:**
- Each adapter is deployable via config and invokes the runtime.
- One integration test per adapter passes.
**Role:** BE, DevOps · **Effort:** L · **Deps:** E3.3

### E3.5 · Pluggable tool registry
**Description:** Drive Gateway targets and tool wiring from a config-based registry (including the name-resolution from E0.3).
**Acceptance criteria:**
- Adding/removing a tool is a config-only change.
- Gateway targets are generated from the registry and names resolve at call time.
**Role:** BE · **Effort:** M · **Deps:** E1.3, E0.3

### E3.6 · Reusable standard-tool library
**Description:** Provide opt-in building-block tools (notification, data CRUD, human-review) usable across clients.
**Acceptance criteria:**
- At least 3 reusable tools packaged and documented.
- A client can enable one via config with no code changes.
**Role:** BE · **Effort:** M · **Deps:** E3.5

---

## Phase 4 — Provisioning, scaffolding & CI/CD

### E4.1 · `create-client` scaffolding CLI  *(flagship)*
**Description:** A generator that produces a new, deployable client project from the template plus a client config.
**Acceptance criteria:**
- One command scaffolds a runnable client project.
- The generated project deploys without manual edits.
**Role:** DevOps · **Effort:** L · **Deps:** Phase 2

### E4.2 · Config → `agentcore.json` / CDK generator
**Description:** Generate `agentcore.json` and CDK parameters from the client config, eliminating manual placeholder edits (discovery URL, client id, Lambda ARNs).
**Acceptance criteria:**
- No `PLACEHOLDER` values remain after generation.
- Generated `agentcore.json` passes `agentcore validate`.
**Role:** DevOps · **Effort:** M · **Deps:** E1.5, E2.4

### E4.3 · Per-client identity provisioning
**Description:** Automate Cognito user pool / app client / AgentCore Identity credential provisioning per client.
**Acceptance criteria:**
- Cognito + Identity are provisioned by script from config.
- No secrets are written to source-controlled files.
**Role:** DevOps, SEC · **Effort:** M · **Deps:** E4.2

### E4.4 · Per-client deploy pipeline
**Description:** Idempotent, repeatable deploy into an isolated account/stack with consistent tagging.
**Acceptance criteria:**
- Re-running deploy is idempotent (no duplicate/failed resources).
- Resources are tagged with the client id; the teardown script is verified.
**Role:** DevOps · **Effort:** L · **Deps:** E4.2

### E4.5 · CI pipeline
**Description:** Lint, unit tests, container build, and dependency/secret scanning on every change.
**Acceptance criteria:**
- CI runs on PRs and blocks on failure.
- The secret scan catches committed credentials.
**Role:** DevOps, QA · **Effort:** M · **Deps:** Phase 0

### E4.6 · Automated eval gates
**Description:** Run AgentCore Evaluations as a gate; block deploys that fall below quality thresholds.
**Acceptance criteria:**
- The eval suite runs pre-deploy.
- Below-threshold results fail the pipeline.
**Role:** QA, AIE · **Effort:** M · **Deps:** E0.9

### E4.7 · Generalized integration/e2e harness
**Description:** Parameterize the existing test scripts by client config for end-to-end verification of any deployment.
**Acceptance criteria:**
- One harness runs e2e against any client config.
- Happy-path, policy-block, and human-review scenarios pass.
**Role:** QA · **Effort:** M · **Deps:** E4.1

---

## Phase 5 — Security, ops, docs & enablement

### E5.1 · Secrets hygiene
**Description:** Remove secrets from `.env`/`agentcore.json`; source them from the Identity vault / Secrets Manager.
**Acceptance criteria:**
- No plaintext secrets in the repo or generated files.
- The runtime retrieves secrets at runtime.
**Role:** SEC · **Effort:** M · **Deps:** E4.3

### E5.2 · Least-privilege IAM + isolation/audit
**Description:** Templated least-privilege roles per resource; per-client data isolation, CloudTrail, tagging, and cost attribution.
**Acceptance criteria:**
- IAM policies are scoped to required actions/resources only.
- Cost and audit reports are attributable per client.
**Role:** SEC · **Effort:** L · **Deps:** E4.4

### E5.3 · Standard observability
**Description:** Bake dashboards and alarms into every deployment.
**Acceptance criteria:**
- Each deploy provisions standard dashboards/alarms.
- Key failure modes alarm (errors, latency, throttles).
**Role:** DevOps · **Effort:** M · **Deps:** E4.4

### E5.4 · Runbooks
**Description:** Operational runbooks for deploy, teardown, credential rotation, and incident response.
**Acceptance criteria:**
- Runbooks exist and are validated by a dry run.
**Role:** DevOps, TW · **Effort:** M · **Deps:** E4.4

### E5.5 · Framework documentation
**Description:** Architecture docs, a config-spec reference, and extension guides.
**Acceptance criteria:**
- The config-spec reference documents every field.
- "Add a tool / agent / trigger" guides exist.
**Role:** TW, SA · **Effort:** L · **Deps:** Phase 2–3

### E5.6 · Onboarding playbook + intake questionnaire
**Description:** A repeatable client-intake process that maps client needs to a config.
**Acceptance criteria:**
- The questionnaire → config mapping is documented.
- One dry-run onboarding is completed end to end.
**Role:** DL, TW · **Effort:** M · **Deps:** E1.2

### E5.7 · Second-domain validation build  *(flagship — Definition of Done)*
**Description:** Build a different domain (e.g., IT-incident or HR request) end to end using only config — the true test of the framework.
**Acceptance criteria:**
- The new domain deploys with config-only changes and zero core edits.
- It passes the generalized e2e harness.
**Role:** AIE, BE, DL · **Effort:** L · **Deps:** Phase 3

### E5.8 · Estimation & engagement template
**Description:** A sizing model and engagement template for onboarding new clients.
**Acceptance criteria:**
- The template estimates effort from a filled intake questionnaire.
- Reviewed and approved with delivery leadership.
**Role:** DL · **Effort:** S · **Deps:** E5.6

---

## Master table — all tasks

| ID | Task | Phase | Role | Effort | Depends on |
|----|------|-------|------|--------|-----------|
| E0.1 | Container build hygiene (.dockerignore) | 0 · Harden | DevOps | S | — |
| E0.2 | Region-safe model IDs | 0 · Harden | AIE | S | — |
| E0.3 | Gateway tool-name namespacing map | 0 · Harden | BE | S | — |
| E0.4 | Autonomous-operation prompt | 0 · Harden | AIE | S | — |
| E0.5 | Forced structured-output decision fallback | 0 · Harden | BE, AIE | M | E0.4 |
| E0.6 | Region resolution hardening | 0 · Harden | BE | S | — |
| E0.7 | Memory identifier sanitization | 0 · Harden | BE | S | — |
| E0.8 | Structured tool-result logging | 0 · Harden | BE | S | E0.3 |
| E0.9 | Baseline regression test suite | 0 · Harden | QA | M | E0.1–E0.8 |
| E1.1 | Core-vs-domain boundary map | 1 · Architecture | SA | M | Phase 0 |
| E1.2 | Client configuration spec | 1 · Architecture | SA | L | E1.1 |
| E1.3 | Extension-point interfaces | 1 · Architecture | SA, BE | M | E1.2 |
| E1.4 | Architecture decision records | 1 · Architecture | SA | S | E1.2 |
| E1.5 | Config schema + validator | 1 · Architecture | BE | M | E1.2 |
| E2.1 | Externalize agent definitions | 2 · Domain extraction | AIE, BE | L | E1.3 |
| E2.2 | Config-driven structured-output schemas | 2 · Domain extraction | BE | M | E2.1 |
| E2.3 | Config-driven routing rules | 2 · Domain extraction | BE | M | E2.1 |
| E2.4 | Parameterize naming/branding | 2 · Domain extraction | BE, DevOps | M | E1.2 |
| E2.5 | Data-model abstraction | 2 · Domain extraction | DE | L | E1.3 |
| E2.6 | Pluggable memory namespaces & actor-id | 2 · Domain extraction | DE, BE | M | E2.5, E0.7 |
| E2.7 | Cedar policy templating | 2 · Domain extraction | SEC | M | E1.3 |
| E2.8 | Optional Guardrails integration | 2 · Domain extraction | SEC | M | E2.7 |
| E3.1 | Configurable orchestration | 3 · Flexible arch | BE, SA | L | E2.1 |
| E3.2 | Pluggable execution phase | 3 · Flexible arch | BE | M | E3.1 |
| E3.3 | Trigger adapter interface + generic trigger | 3 · Flexible arch | BE, DevOps | M | E1.3 |
| E3.4 | Additional trigger adapters (API/schedule/queue) | 3 · Flexible arch | BE, DevOps | L | E3.3 |
| E3.5 | Pluggable tool registry | 3 · Flexible arch | BE | M | E1.3, E0.3 |
| E3.6 | Reusable standard-tool library | 3 · Flexible arch | BE | M | E3.5 |
| E4.1 | create-client scaffolding CLI | 4 · Provisioning/CI | DevOps | L | Phase 2 |
| E4.2 | Config → agentcore.json / CDK generator | 4 · Provisioning/CI | DevOps | M | E1.5, E2.4 |
| E4.3 | Per-client identity provisioning | 4 · Provisioning/CI | DevOps, SEC | M | E4.2 |
| E4.4 | Per-client deploy pipeline | 4 · Provisioning/CI | DevOps | L | E4.2 |
| E4.5 | CI pipeline | 4 · Provisioning/CI | DevOps, QA | M | Phase 0 |
| E4.6 | Automated eval gates | 4 · Provisioning/CI | QA, AIE | M | E0.9 |
| E4.7 | Generalized integration/e2e harness | 4 · Provisioning/CI | QA | M | E4.1 |
| E5.1 | Secrets hygiene | 5 · Security/Ops/Docs | SEC | M | E4.3 |
| E5.2 | Least-privilege IAM + isolation/audit | 5 · Security/Ops/Docs | SEC | L | E4.4 |
| E5.3 | Standard observability | 5 · Security/Ops/Docs | DevOps | M | E4.4 |
| E5.4 | Runbooks | 5 · Security/Ops/Docs | DevOps, TW | M | E4.4 |
| E5.5 | Framework documentation | 5 · Security/Ops/Docs | TW, SA | L | Phase 2–3 |
| E5.6 | Onboarding playbook + intake questionnaire | 5 · Security/Ops/Docs | DL, TW | M | E1.2 |
| E5.7 | Second-domain validation build | 5 · Security/Ops/Docs | AIE, BE, DL | L | Phase 3 |
| E5.8 | Estimation & engagement template | 5 · Security/Ops/Docs | DL | S | E5.6 |

**Totals:** 43 tasks · Phase 0 (9) · Phase 1 (5) · Phase 2 (8) · Phase 3 (6) · Phase 4 (7) · Phase 5 (8).
