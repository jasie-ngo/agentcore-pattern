# Event-Driven Claims Agent — AI Coding Assistant Context

> **For humans:** This file provides context for AI coding assistants (Kiro, Cursor, Claude Code, GitHub Copilot). For the human-readable documentation, see [docs/](./docs/README.md), [README.md](./README.md), or [SWITCHING_USE_CASES.md](./SWITCHING_USE_CASES.md).

This project is an **event-driven agent platform** built on Amazon Bedrock AgentCore. One Runtime (`multiusecaseagent`) serves one of several YAML-defined use cases (strands-compose), selected by `USE_CASE` in `app/main.py`: `claims_agent` (insurance claims — dual-agent Claims Processor + Validation Agent, Sonnet/Haiku cost routing, deterministic execution), `hesta_agent` (HESTA member-email pipeline) or `card_dispute_agent` (bank card disputes, built only from agent/tool blueprints and a decision table). Deploys as a single CloudFormation stack (`AgentCore-ClaimsAgent-dev`) via the AgentCore CLI. See [SWITCHING_USE_CASES.md](./SWITCHING_USE_CASES.md).

> **Important:** AgentCore resources (Runtime, Gateway, Memory, PolicyEngine, OnlineEval) are declared in `agentcore/agentcore.json` and managed by the AgentCore CLI. Supplementary infrastructure (DynamoDB, Lambda tools, SNS, S3, EventBridge) is defined in the TypeScript CDK app at `agentcore/cdk/lib/infra-construct.ts`. The Cognito User Pool (Gateway M2M auth) is managed by `scripts/setup_cognito.sh` (AWS CLI, not CDK). Use `agentcore validate` and `agentcore dev` while iterating; run `agentcore deploy --target dev` to deploy everything together.

---

## Architecture

```
S3 upload (claims-inbox/)
  → EventBridge rule
    → Trigger Lambda (lambdas/trigger/handler.py)
        reads S3 object, invokes Runtime via SigV4-signed HTTPS (fire-and-forget)
      → AgentCore Runtime (Container: app/, use case = app/main.py USE_CASE)
          claims_agent (delegate orchestration, coordinator on Haiku):
            Phase 1: claims_processor (Sonnet, assessor blueprint) → lookup_policy → ACCEPT/REJECT
            Phase 2: claims_validator (Haiku, reviewer blueprint) → checks + approved + confidence
            Phase 3: execute_claim_decision tool (no LLM) → decisions.yaml → create_claim / human_review / send_notification
        → AgentCore Gateway (MCP, Cognito CUSTOM_JWT auth, Cedar policy enforcement)
            → tool Lambdas declared in app/<use_case>/gateway_tools.yaml
              (blueprints: lambdas/blueprints/*, custom: lambdas/<use_case>/<tool>)
```

**Auth (two separate paths):**
- **Inbound to Runtime (Trigger Lambda → Runtime):** AWS_IAM (SigV4). The Trigger Lambda's execution role has `bedrock-agentcore:InvokeAgentRuntime` permission granted by CDK via `runtime.grantInvoke(triggerFn)`. No Cognito credentials needed.
- **Outbound from Runtime to Gateway (Runtime → MCP Gateway):** Cognito M2M JWT via `@requires_access_token(provider_name="cognito-gateway-m2m", auth_flow="M2M")` decorator. Secrets managed by AgentCore Identity vault (registered via `agentcore add credential`). The Gateway validates JWT via CUSTOM_JWT authorizer (Cognito OIDC discovery).

---

## Directory Structure

```
event-driven-claims-agent/
├── AGENTS.md                          # This file
├── CLAUDE.md                          # Claude Code guidance
├── README.md                          # Full project documentation
├── deploy.sh                          # One-command deploy (runs CDK)
├── SWITCHING_USE_CASES.md             # How to switch / add use cases
├── app/                               # Runtime codeLocation (whole folder is the container)
│   ├── Dockerfile                     # Multi-stage, Python 3.12, ARM64
│   ├── main.py                        # create_app(<USE_CASE>/config.yaml) — the one-line switch
│   ├── pyproject.toml, uv.lock        # Dependencies for every use case (uv-managed)
│   ├── _shared/                       # Generic plumbing, no domain logic
│   │   ├── factory.py                 # Agent factory: structured output + Gateway attachment
│   │   ├── gateway.py                 # Identity-managed Gateway MCP client, GATEWAY_TOOL_NAMES
│   │   ├── memory.py                  # AgentCore Memory session manager (local fallback)
│   │   ├── ingestion.py               # Generic message normaliser → envelope (stdlib only; bundled into the Trigger)
│   │   ├── agent_blueprints.py        # Agent blueprints: classifier, extractor, assessor, reviewer, writer
│   │   ├── decision_table.py          # Decision-table engine (stdlib only)
│   │   ├── local_tools.py             # decision_table_tool / ingestion_tool — forms → agent tools
│   │   └── settings.py                # Generic env vars (region, Gateway)
│   ├── claims_agent/
│   │   ├── config.yaml                # Agents (assessor + reviewer blueprints), models, orchestration
│   │   ├── decisions.yaml             # route_claim: REJECT / AUTO_APPROVE / HUMAN_REVIEW (AUTO_APPROVE_THRESHOLD)
│   │   ├── gateway_tools.yaml         # Gateway tools this use case owns (blueprint + custom)
│   │   ├── ingestion.yaml             # How inbound submissions are cleaned up
│   │   └── tools.py                   # execute_claim_decision (deterministic Phase 3 — the one hand-written piece)
│   ├── hesta_agent/                   # HESTA member-email use case — blueprints only: config.yaml (5 blueprint agents),
│   │                                  #   decisions.yaml (verify_identity, check_attachments, route_case), ingestion.yaml,
│   │                                  #   gateway_tools.yaml (`uses:` + list_member_cases), tools.py (bindings)
│   └── card_dispute_agent/            # Bank card disputes (fictional Harbour Bank) — blueprints only: config.yaml, gateway_tools.yaml
│                                      #   (creates its own tables), decisions.yaml, ingestion.yaml, seed_data.yaml, sample_emails/
├── lambdas/                           # Gateway tool Lambdas (declared in app/<use_case>/gateway_tools.yaml)
│   ├── blueprints/retrieval/          # Blueprint: read records (configured by TOOL_CONFIG)
│   ├── blueprints/record_write/       # Blueprint: create / find-or-create records
│   ├── claims_agent/notification/     # Custom tool: SES email (handler.py + schema.json)
│   ├── claims_agent/resolve_claim/    # Custom tool: approve/deny claim + close reviews
│   └── trigger/handler.py             # S3 → pick use case → envelope → Runtime (SigV4 auth)
├── agentcore/
│   ├── agentcore.json                 # Declarative AgentCore resources (Runtime/Gateway/Memory/PolicyEngine/Eval); Gateway targets are generated
│   ├── generated/                     # Built by scripts/build_tools.py — tool schemas + manifest.json (never edit)
│   ├── aws-targets.json               # Deployment targets (account + region)
│   └── cdk/lib/
│       ├── infra-construct.ts         # Supplementary AWS infra (DynamoDB, S3, SNS, EventBridge, Lambdas — Cognito is script-managed)
│       └── cdk-stack.ts               # Integration: wires infra ARNs + JWT authorizer + runtime env vars
├── scripts/
│   ├── build_tools.py                 # gateway_tools.yaml + ingestion.yaml → schemas, Gateway targets, manifest
│   ├── seed_tables.py                 # Load app/<use_case>/seed_data.yaml into the tables that use case created
│   ├── deploy.sh                      # Deploy helper
│   ├── destroy.sh                     # Unified teardown (observability → stack → orphans → Cognito → state)
│   ├── cleanup_agentcore.py           # Delete orphaned AgentCore control-plane resources (boto3)
│   ├── setup_cognito.sh               # Create Cognito User Pool via AWS CLI (not CDK)
│   ├── teardown_cognito.sh            # Delete Cognito if script-created
│   ├── enable_observability.py        # Enable Transaction Search + Gateway/Memory deliveries
│   ├── disable_observability.py       # Clean up observability deliveries
│   ├── seed_dynamodb.py              # Populate test policies
│   ├── test_invoke.py                # Direct Runtime invocation (SigV4 auth)
│   ├── test_auth.py                  # Authentication pattern tests (6 scenarios)
│   ├── test_e2e.py                   # Full E2E test suite (5 scenarios)
│   ├── test_cedar.py                 # Cedar policy enforcement tests
│   ├── test_local.py                 # Local dev invocation helper
│   └── lint.sh                       # py_compile + ruff checks
├── tests/                             # Offline unit tests (pytest)
│   ├── test_use_case_decisions.py     # Claims + HESTA decision tables (routing, identity, attachments)
│   ├── test_claims_tools.py           # execute_claim_decision against a fake Gateway
│   ├── test_lambda_handlers.py        # Blueprint tools, loaded with the real generated settings
│   ├── test_ingestion.py              # Generic normaliser + each use case's ingestion.yaml
│   ├── test_build_tools.py            # Generator: generated files fresh, validation rules
│   ├── test_agent_blueprints.py       # Agent blueprints, tool builders, every use case's config
│   ├── test_decision_table.py         # Decision-table engine + every card-dispute scenario
│   ├── test_trigger.py                # Trigger Lambda
│   └── sample-claim-email.txt        # Email for E2E test 5 (uses POL-67890)
├── docs/
│   ├── FRAMEWORK.md                  # Framework architecture: use cases, envelope, blueprint tools, build step
│   ├── ARCHITECTURE.md               # System design and data flows
│   ├── deployment.md                 # Step-by-step deploy, verify, teardown
│   ├── decisions/                    # Architectural decision records (ADR-0001..0010)
│   └── CONFIGURATION.md             # All config surfaces reference
```

---

## Build, Test, Deploy

### Deploy everything
```bash
./deploy.sh [region]          # defaults to us-west-2
```

This runs: configure target → npm install (CDK) → uv sync (agent) → `scripts/build_tools.py` (Gateway tools) → `agentcore validate` → cdk bootstrap → `agentcore deploy --target dev` → seed DynamoDB → prints test commands.

### Manual AgentCore / CDK operations
```bash
agentcore validate                       # validate agentcore.json
agentcore deploy --target dev --yes      # deploy everything

# NOTE: agentcore CLI does NOT have a destroy command. Use the destroy script:
./scripts/destroy.sh us-west-2           # full teardown (handles DELETE_FAILED + orphans)

# Or drive the underlying TypeScript CDK directly:
cd agentcore/cdk && npm install && npx cdk diff
```

### Invoke the agent (requires deployed stack)
```bash
python3 scripts/test_invoke.py --region us-west-2
python3 scripts/test_invoke.py --region us-west-2 --prompt 'File a claim for POL-12345. $5000 storm damage.'
```

### Run E2E tests
```bash
python3 scripts/test_e2e.py --region us-west-2
python3 scripts/test_e2e.py --region us-west-2 --test 2   # Cedar block test
```

### Run authentication pattern tests
```bash
python3 scripts/test_auth.py --region us-west-2          # All 6 auth tests
python3 scripts/test_auth.py --region us-west-2 --test 3  # Single test (Runtime→Gateway M2M)
```
Validates: (1) SigV4→Runtime succeeds, (2) JWT→IAM Runtime rejected, (3) Runtime→Gateway M2M
works via tool call, (4) no-auth rejected, (5) invalid JWT rejected, (6) wrong scope rejected.

### Run unit tests (offline)
```bash
python3 -m pytest tests/          # routing, structured output, lambda handlers, trigger
```

### Lint
```bash
./scripts/lint.sh
# or manually:
find app/ lambdas/ scripts/ -name "*.py" -exec python3 -m py_compile {} \;
```

---

## Key Invariants

1. **Lambda handlers return `json.dumps({...})` directly** — no `{statusCode, body}` envelope. The Gateway strips the HTTP wrapper.
2. **Agent routing controls claim status** — the `create_claim` Lambda accepts `status` and `decision` as optional parameters from the agent. Do not add routing logic to the Lambda itself.
3. **Gateway tools are declared in `app/<use_case>/gateway_tools.yaml`** — blueprint tools (`retrieval`, `record_write`) are a form entry, custom tools point at `lambdas/<use_case>/<tool>/`. After editing, run `uv run --project app python scripts/build_tools.py`: it writes `agentcore/generated/` (schemas + manifest) and the Gateway `targets` in `agentcore.json`. Never edit those by hand; CDK refuses to synth if they are stale. See `docs/FRAMEWORK.md`.
4. **Container build, not CodeZip** — runtime deps go in `app/pyproject.toml` (managed by `uv`; run `uv lock` in `app/` after changing them). The Dockerfile runs `uv sync --frozen`. `codeLocation` is the whole `app/` folder; switching use case is `USE_CASE` in `app/main.py`, never an `agentcore.json` edit.
5. **`agentcore/agentcore.json` is the source of truth for AgentCore resources** (Runtime, Gateway, Memory, PolicyEngine, OnlineEval). Supplementary AWS infra is in `agentcore/cdk/lib/infra-construct.ts`; `cdk-stack.ts` wires the two together (patches Lambda ARNs + the Gateway CUSTOM_JWT authorizer discovery URL, injects runtime env vars). Don't hand-edit generated CDK output.
6. **Two auth paths:** Inbound to Runtime uses AWS_IAM (SigV4) — CDK grants `runtime.grantInvoke()` to the Trigger Lambda. Outbound from Runtime to Gateway uses Cognito M2M JWT via `@requires_access_token(provider_name="cognito-gateway-m2m", auth_flow="M2M")` — secrets live in AgentCore Identity vault, not env vars.
7. **Structured output comes from the blueprints** — the claims processor (assessor) returns outcome/confidence/reasons + claim facts, the validator (reviewer) returns checks/approved/confidence/issues; no use case has a `schemas.py`. `claims_agent/decisions.yaml` (`route_claim`) turns them into the action with safe defaults: no decision → REJECT; anything not approved with confidence ≥ `AUTO_APPROVE_THRESHOLD` → HUMAN_REVIEW.
8. **Phase 3 is deterministic (no LLM picks tools)** — `claims_agent/tools.py:execute_claim_decision` applies `route_claim` and calls Gateway tools directly via `MCPClient.call_tool_async()` using structured data from Phase 1/2. All Phase 3 actions are non-fatal (log + continue on failure).
9. **`_shared/` stays use-case-neutral** — no domain names, prompts or thresholds there; those live in the use case folder.
10. **Inbound messages arrive as an envelope** — the Trigger normalises every S3 upload with `app/_shared/ingestion.py` + `app/<use_case>/ingestion.yaml` and sends JSON with `envelope_version` as the prompt. `ingestion.py` must stay standard-library only (it is bundled into the Trigger Lambda). Uploads route by folder: `claims-inbox/<use_case>/<file>`.
11. **Prefer blueprints.** A new agent role should be an agent blueprint filled in from `config.yaml` (`../_shared/agent_blueprints.py:<role>`); a deterministic rule should be a decision table (`decisions.yaml`). Hand-written prompts and `@tool` decision code are for what those cannot express. `decision_table.py` stays standard-library only.

---

## Environment Variables

### Runtime container (set by CDK)
| Variable | Purpose |
|---|---|
| `AGENTCORE_GATEWAY_URL` | MCP Gateway HTTPS endpoint |
| `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER` | Identity credential provider name (no secrets) |
| `AGENTCORE_GATEWAY_OAUTH_SCOPES` | `agentcore/invoke` |
| `AGENT_OBSERVABILITY_ENABLED` | `true` — enables OTEL instrumentation |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `true` — captures LLM messages in traces |
| `AGENT_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` |
| `FAST_MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` — used by Validation Agent (Phase 2) |
| `AUTO_APPROVE_THRESHOLD` | `80` — confidence threshold for auto-approval |

### Lambda functions (set by CDK)
| Variable | Lambda(s) | Value |
|---|---|---|
| `TABLE_NAME`, `TOOL_CONFIG` | blueprint tools (lookup_policy, list_pending_claims, create_claim, request_human_review) | the tool's table; its gateway_tools.yaml entry as JSON |
| `NOTIFY_TOPIC_ARN` | request_human_review | SNS topic ARN |
| `CLAIMS_TABLE` | resolve_claim | `ClaimsAgent-dev-Claims` |
| `REVIEWS_TABLE` | resolve_claim | `ClaimsAgent-dev-Reviews` |
| `SENDER_EMAIL` | notification | SES verified sender |

### Trigger Lambda (set by CDK)
| Variable | Purpose |
|---|---|
| `AGENTCORE_RUNTIME_ARN` | Runtime ARN for SigV4-signed HTTPS invocation |

---

## Test Policies (seeded by `seed_dynamodb.py`)

| Policy Number | Holder | Type | Coverage | Status |
|---|---|---|---|---|
| `POL-12345` | John Smith | auto | $50,000 | active |
| `POL-67890` | Jane Doe | home | $250,000 | active |
| `POL-11111` | Bob Johnson | auto | $75,000 | active |
| `POL-99999` | Alice Williams | auto | $100,000 | expired |

---

## Cedar Policies

Two policies (in `agentcore/agentcore.json` under `policyEngines`) enforce authorization at the Gateway:
- **AllowAllTools** — `permit(principal, action, resource is AgentCore::Gateway)`
- **BlockExcessiveClaims** — `forbid` when `context.toolName == "create-claim"` and `context.input.estimated_amount >= 100000`

Both use `IGNORE_ALL_FINDINGS` validation mode (required for the permit-all policy).
