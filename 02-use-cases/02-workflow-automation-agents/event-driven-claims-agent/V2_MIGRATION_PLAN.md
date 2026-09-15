# ClaimsAgentV2 (HESTA) — Migration & Pre-Deploy Evaluation Plan

> Status: Round 5 — all known ambiguities resolved to single, concrete paths. Open questions
> closed. **No code has been changed as part of this round — this file only.** Code stays
> frozen until you say "ignore master command."

## 0. Decisions locked in (from your review)

| # | Topic | Decision | Status |
|---|---|---|---|
| 1 | Old stack safety | Verified from the CLI's own `toStackName()` logic — a rename produces a fully separate CloudFormation stack name. The old stack cannot be touched by *that* mechanism. **Superseded in part by §1 below** — a separate problem (resource name collisions) can still affect it. | Verified, see §1 |
| 2 | Cedar $100k guardrail | Not needed for the new HESTA tools. | Decided — no action |
| 3 | Orphaned Lambdas / dead agent modules | Confirmed fine to leave archived/deleted (4 duplicate Lambdas removed, 2 dead agent modules moved to `agents/old-version/`). | Done |
| 4 | External web-search / non-AWS MCP tool | Verified: none exists anywhere in this repo (checked both `app/claimsagent` and `app/hesta-claimsagent`). Nothing to remove. | Verified — no action needed |
| 5 | `app/claimsagent` (original demo) | Keep permanently as reference. | Decided — do not touch |
| 6 | Local test scripts | **Revised.** Not "leave untouched forever" — the files themselves stay (never delete), but their *content* gets updated wherever needed so they actually test the new V2 stack/pipeline instead of the old one. Covers all 6: `test_invoke.py`, `test_auth.py`, `test_cedar.py`, `test_e2e.py`, `test_local.py`, `get_token.py`. See §4.4. | Decided — see §4.4 |
| 7 | Old stack must stay untouched; new deploy must be a genuinely separate/new runtime | Confirmed as the **only** hard requirement regarding the old stack — its long-term fate (kept running, retired, etc.) is explicitly out of scope for this plan; not something to design for here. The one rule: never touch, modify, or delete it. | Requirement confirmed; fix needed, see §1 |
| 8 | Resource tagging | Add tag `id: 2520121` to everything the new stack creates, in addition to the CLI's automatic `agentcore:project-name`/`agentcore:target-name` tags. | Decided — action queued for when code is unfrozen |
| 9 | Documentation refresh | Do it, but as the **last** step of this plan, not before. | Decided — see §7 |
| 10 | Resource inventory | Add a full list of every AWS resource this app creates, for reference. | Done — see §8 |
| 11 | Branch layout confirmed | Verified from git history: the **old** stack (`AgentCore-ClaimsAgent-dev`) was deployed from `feature/hesta-ecf-demo` (still has the old `agentcore.json`/6-Lambda code intact). This branch, `feature/hesta-ecf-evaluation`, branched off it at commit `5e1a760` and carries the rename + new-Lambda changes that will become `ClaimsAgentV2`. Code for both versions genuinely exists in the repo, one per branch; deployed, they run side-by-side as two separate stacks. | Verified |
| 12 | POC scope boundary | Confirmed: the POC's responsibility ends once the relevant record (case or human-review) is written to DynamoDB — that write **is** the hand-off. Anything past that (richer identity verification, production guardrails, auto-notifications, dashboards) is explicitly "full project" scope, tracked separately, not to creep into this POC. | Decided |
| 13 | SNS `ReviewTopic` (`ClaimsAgent-HumanReview`) | Drop entirely — nothing publishes to it (decision 12: the DynamoDB write is the hand-off, no notification needed). Remove the `sns.Topic` construct and its `CfnOutput` from `infra-construct.ts`. | Decided — action queued |
| 14 | Cognito User Pool / M2M client | Separate, new, project-qualified pool + client for `ClaimsAgentV2` — not shared with the old stack's pool. Follows directly from decision 2/7 ("everywhere use a new name, never touch the old deployment") — Cognito is no exception. | Decided — action queued |

## 1. Critical finding: resource-name collisions with the still-live old stack — fix path now singular

You confirmed (decision 7) that the old stack must stay untouched and the new deploy must be a genuinely separate, new runtime. As currently written, **it isn't** — most of the shared "plumbing" resources in `infra-construct.ts` reuse the exact same hardcoded names the old, already-deployed stack uses.

**What's safe (correctly unique, verified):**
- The 3 new DynamoDB tables (`Hesta-members-*`, `Hesta-cases-*`, `Hesta-humanreview-*`) — new names, no overlap with the old `Policies`/`Claims`/`Reviews` tables.
- The 3 new Gateway-tool Lambdas (`ClaimsAgent-MemberLookup`, `ClaimsAgent-CaseLookupCreation`, `ClaimsAgent-EmailReview`) — new names, no overlap with the old 6 tool Lambdas.

**What will collide (same account, same region as the old stack):**

| Resource | Hardcoded name | Problem |
|---|---|---|
| S3 inbox bucket | `claims-inbox-{account}-{region}` | No project-name component at all. S3 bucket names are **globally unique** — the old stack already owns this exact name, so creating it again will fail outright. |
| EventBridge rule | `ClaimsAgent-InboxTrigger` | Literal, unchanged, already exists on the old stack. |
| Trigger Lambda | `ClaimsAgent-Trigger` | Same. |
| Dead-letter queue | `ClaimsAgent-TriggerDLQ` | Same. |
| CloudWatch alarm | `ClaimsAgent-FailedClaims` | Same. |
| Bedrock guardrail | `ClaimsAgent-{target}-NoPersonalAdvice` | Only varies by *target* ("dev"), not by *project* — both the old and new stack resolve this to the same name. |

(The SNS topic `ClaimsAgent-HumanReview` was also on this list — resolved by decision 13: dropped entirely rather than renamed, since nothing uses it.)

**The fix — ONE concrete path, decided (no more "or"):** use the **full CloudFormation stack name** (`cdk.Stack.of(this).stackName`, e.g. `AgentCore-ClaimsAgentV2-dev`) as the base for every resource name that currently collides, and audit every *other* hardcoded name in `infra-construct.ts` for the same treatment. This is already available with zero new prop-plumbing (no need to pass `spec.name` in separately — `stack.stackName` already contains the full project name). Concretely:
- S3 bucket: derive from the full stack name (e.g. `claims-inbox-{stack.stackName.toLowerCase()}-{account}-{region}`, respecting S3's lowercase/character-set rules), not just account+region.
- EventBridge rule, Trigger Lambda, DLQ, CloudWatch alarm, Bedrock guardrail: prefix/suffix each literal with the full `stack.stackName` instead of the bare literal or the last-segment-only suffix.
- Remove the SNS `ReviewTopic` construct entirely (decision 13) rather than renaming it.
- Re-audit the 3 DynamoDB tables and 3 tool Lambdas too — they're safe *today* only because their concepts are new, not because the pattern is inherently collision-proof. Bring them onto the same full-stack-name-derived pattern going forward for consistency.
- **Cognito (decision 14):** create a separate, project-qualified User Pool + M2M app client for `ClaimsAgentV2` via `setup_cognito.sh` (or equivalent), rather than pointing at the old stack's pool. This isn't a rename-in-place like the CDK resources above — it's a distinct pool, created fresh.
- **Validation checkpoint A (§5) checks for this exhaustively — zero hardcoded literals, zero names built only from the environment/target suffix, everywhere in the file — plus confirms the new stack's Cognito pool ID differs from the old stack's.**

## 2. Current repo state — condensed audit status

### 2a. Confirmed bugs (not yet fixed — code frozen)
| # | File | Issue |
|---|---|---|
| B1 | `scripts/seed_hesta_members.py` | `IndentationError` in `get_table_name()` — confirmed via `py_compile`. |
| B2 | `scripts/seed_hesta_members.py` | The whole script was built for the *old* schema — records use `policy_number` as the key, not `member_id` (the new `Hesta-members` table's actual key). Needs a rebuild of the record structure — see §4.3 for the now-concrete approach. |
| B3 | `agentcore/agentcore.json` | `onlineEvalConfigs[0].agent` still says `"hestaclaimsagent"`; the only declared runtime is `"hestaclaimsagent_v2"`. |
| B4 (new, minor, non-blocking) | `app/hesta-claimsagent/models.py` | `MemberProfile.match_key`/`factors_matched` field *descriptions* still reference the old concept ("member_number", "account_type") while the actual code populates them with `"member_id"`-labeled values. Cosmetic only — doesn't crash anything — but worth a docstring cleanup while touching this area. |

### 2b. Resolved this round
- Archived 2 dead agent modules (`identity_profiling.py`, `case_status.py`) — confirmed unused, called Gateway tools that no longer exist.
- Deleted 4 redundant, half-edited Lambda copies (`create_claim`, `human_review`, `list_pending_claims`, `resolve_claim`) — true originals already safe in `lambdas/old-version/`.
- Pruned `tools/gateway.py`'s `GATEWAY_TOOL_NAMES` map to the 3 live tools; updated its stale "no new Lambdas" docstring.
- Confirmed DynamoDB tables ↔ Lambda env vars ↔ Gateway targets ↔ schema files are fully consistent (table in §8).
- Traced `case_lookup_creation` → `context_manager.py` → `CaseInfo` mapping and the `InboundEmail.member_number_for_lookup`/`from_email` fields the identity gate depends on — both verified correct, no bug.

### 2c. Still outstanding
- README.md, AGENTS.md, `docs/ARCHITECTURE.md`, `docs/deployment.md`, `docs/tutorial.md`, `docs/CONFIGURATION.md` — all still describe the old 6-Lambda / insurance-claims architecture. Queued for §7 (last step).
- `app/hesta-claimsagent/IMPLEMENTATION_PLAN.md` §0/§6.1/§8 still frame the new Members/Cases/HumanReview tables as "post-pilot, not yet built" — now contradicted by the shipped code. Also queued for §7.

## 3. AI-capability build order — review against the priority slide

You shared a slide ranking 7 capabilities in build-priority order, plus 2 "foundational, always-on" ones. Checked each against what's actually in `main.py`'s pipeline today:

| Slide rank | Capability | Built? | How it runs today |
|---|---|---|---|
| 1 | AI-001 Intent Identifier + attachment detection | ✅ Done | Runs first, exactly as ranked. |
| 2 | AI-002 Conversation Context Manager | ✅ Done | Runs second, exactly as ranked — **but see AI-003 below, it's merged in here.** |
| 3 | AI-003 Identity & Profiling | ✅ Done, **not standalone** | The identity lookup (`member_lookup`) happens *inside* AI-002's function, not as its own separately-trackable step. The originally-separate `identity_profiling.py` module is dead code (archived — it called tools that no longer exist). |
| 4 | AI-005 Empathy | ✅ Done, standalone | Own module (`agents/empathy.py`), runs in the Decide phase as its own step — matches the slide. |
| 5 | AI-011 Writer | ✅ Done, standalone | Own module, runs in Execute. Takes empathy (`emp`) as input; will also take attachment output once §4.2 lands. |
| 6 | AI-012 Reviewer & Editor | ✅ Done, standalone | Own module, runs right after Writer. Takes neither empathy nor attachment output as input — only the draft, intent, and profile. |
| 7 (lowest) | AI-004 Attachment Validation | ✅ Done, currently isolated | Own module, runs in Decide — deterministic Python (no LLM), output not yet consumed by Writer/Reviewer/routing. Fixed by §4.2. |
| F | AI-013 Human Feedback Learning | ❌ Not built | Deliberate per `IMPLEMENTATION_PLAN.md` — reuse-only, no new build in this POC (confirmed intentional, not a gap). |
| F | AI Dashboard | ❌ Not built | Same — substituted by raw AgentCore OTEL traces + evaluator config, deliberate for this POC. |

**Headline takeaway:** the build didn't follow any phased priority — the pilot built the full pipeline in one pass rather than incrementally. Against the slide specifically: AI-004 was over-built relative to its lowest ranking (fixed by making its output actually useful, §4.2); AI-003 has no independent identity as a module (acceptable — see §0 decision 12, out of POC scope to fully separate); AI-013/Dashboard being unbuilt is by design, not a gap.

## 4. Pipeline design decisions (POC-scoped — full production version deferred, per decision 12)

### 4.1 Identity gate (simplified, POC-level)

Rule: **if `member_lookup` returns a `member_id`, proceed through the full pipeline. If it doesn't, skip straight to a reduced path.**

Reduced path when no `member_id`:
```
AI-001 → AI-002 (context + identity attempt) → [skip AI-004, skip AI-005]
      → AI-011 Writer (drafts an identity-verification-request reply)
      → [skip AI-012 Reviewer] → flag for human review
```

Deferred to full project (not this POC): partial-match verification levels, cross-checking sender email against a matched record, name+DOB fallback matching — the POC rule is binary (member found vs. not).

### 4.2 Attachment validation (AI-004) wired as real input, not just display

Two changes:
- Pass AI-004's output (`attach`) into `writer_agent.write()`, alongside the existing `emp` (empathy) input — so the draft can actually ask for a missing document, or acknowledge "we've received your attachment and are reviewing it" for the `present_unverified` case.
- Include the attachment status/notes in the `email_review` (human-review) record write — right now the reviewer only sees `decision.reasons` (routing escalation reasons); they should also see "document missing" or "document present but unverified."

### 4.3 DynamoDB seeding — rebuild, with a concrete extraction method (resolves the earlier ambiguity)

**Problem, confirmed by reading the script:** `scripts/seed_hesta_members.py` was built entirely for the *old* schema (`policy_number` key). Never adapted for `Hesta-members` (key `member_id`).

**Also confirmed:** only `Hesta-members` needs pre-seeding — `Hesta-cases` and `Hesta-humanreview` are populated on-demand by their own Lambdas.

**Concrete rebuild approach:**
- Rebuild the seed script's record structure around `member_id` (not `policy_number`), keeping `email`/`name`/`status` and useful extra fields (dob, address, scenario tag).
- **Extraction method — reuse existing code, don't write a second parser:** run each of the 23 files in `hesta/sample-emails/` through the already-built `ingestion/email_normalizer.normalize_email()` (the same function the live pipeline uses) to get `member_number_for_lookup` and `from_email` consistently, regardless of which of the two email shapes (contact-form vs. threaded email) the sample uses — that logic already exists and is tested by construction (it's what the real pipeline runs on). Only the display **name** needs a small separate extraction (from the `From:` header for direct emails, or the `name` field for contact-form samples) since the normalizer doesn't currently surface that as a distinct field.
- Derive one seeded member per sample from these extracted values, instead of inventing disconnected synthetic data.
- **Deliberately omit 1–2 samples' identifiers from the seed set on purpose** — gives a genuine test case for the §4.1 "no member_id → reduced path → human review" flow.
- Point the script at the new stack/table, not the old one.

### 4.4 Local test scripts — update content for the new use case (revises decision 6)

Not a blanket "leave alone." Go through all 6 and update whichever parts test something that's actually changed:
- `test_invoke.py`, `test_auth.py`, `test_cedar.py`, `test_local.py`, `get_token.py` — currently hardcode the old stack name (`AgentCore-ClaimsAgent-dev`). Update the stack-name references to the new stack; where they assert on old tool names (e.g., `test_cedar.py`'s Cedar $100k scenarios), either adapt to whatever the new Gateway's policy actually enforces (currently just `ClaimsAllowAllTools` — see decision 2) or remove the now-inapplicable assertions.
- `test_e2e.py` — same treatment, but it also currently has one already-half-updated line (new table name) sitting alongside all-old tool-name assertions (`create_claim`, `notification`, `request_human_review`). Rewrite its scenarios around the new tools (`member_lookup`, `case_lookup_creation`, `email_review`) and the new behavior (draft displayed, no auto-send, identity-gate reduced path from §4.1).
- Files themselves are never deleted — only content changes.

## 5. Step-by-step plan, with validation checkpoints

Each checkpoint below exists specifically to prove the old stack and its resources were **not** affected — that's the point of this whole exercise, per decision 7.

### Phase 0 — Fix blockers
- [ ] Fix the name collisions per §1's single decided path (full `stack.stackName`-derived names, everywhere, not just the known ones) — including removing the SNS `ReviewTopic` construct (decision 13) and standing up a separate Cognito pool/client for `ClaimsAgentV2` (decision 14).
- [ ] Fix B1/B2 — rebuild `seed_hesta_members.py` per §4.3 (reuse `email_normalizer`, sample-derived records, deliberate omissions, correct target stack).
- [ ] Fix B3 (`onlineEvalConfigs[0].agent` → `hestaclaimsagent_v2`).
- [ ] Fix B4 (optional cleanup — `MemberProfile` field descriptions).
- [ ] Update the local test scripts per §4.4.
- [ ] Implement §4.1 (identity gate / reduced path) and §4.2 (attachment output wired into Writer + human-review record).

**✅ Validation checkpoint A — before touching AWS at all (strictly deploy-independent checks only):**
- `agentcore validate` passes.
- `python3 -m py_compile` clean across `scripts/` and `lambdas/`.
- `python3 -m pytest tests/` still green.
- Re-read the fixed `infra-construct.ts` top to bottom and confirm **every** resource name is built from the full `stack.stackName` — zero hardcoded literals, zero names built only from the environment/target suffix, SNS topic construct removed, Cognito pointed at a new pool.
- Run the rebuilt `seed_hesta_members.py --dry-run` and manually check a couple of the printed records against 2–3 real sample emails to confirm the member numbers/names/emails actually match.

**Note:** the AgentCore-Memory recall check does *not* belong here — `agentcore dev` still makes real AWS calls, and this project's Memory resource doesn't exist until Phase 3's deploy creates it. That check lives in Checkpoint C only (below), where it's actually testable.

### Phase 1 — Local validation (no AWS deploy)
- [ ] `agentcore dev` locally against `hesta-claimsagent`; confirm it boots and fails gracefully without a live Gateway.
- [ ] `./scripts/lint.sh`.

**✅ Validation checkpoint B:**
- `agentcore deploy --dry-run` and `agentcore deploy --diff` — confirm the plan lists **only new resource names**, and that the diff shows zero entries referencing the old stack (`AgentCore-ClaimsAgent-dev`) in any form.

### Phase 2 — Pre-flight for the real deploy
- [ ] Region/account alignment check (`aws configure get region`, `aws-targets.json`, `aws sts get-caller-identity`).
- [ ] IAM permission check for `iam:CreateRole` on `*BedrockAgentCore*` roles.
- [ ] **Snapshot the old stack first** — `aws cloudformation describe-stacks --stack-name AgentCore-ClaimsAgent-dev` and `aws cloudformation list-stack-resources --stack-name AgentCore-ClaimsAgent-dev`, save the output. This is the "before" picture the next checkpoint compares against.

### Phase 3 — Deploy `ClaimsAgentV2` side-by-side
- [ ] `agentcore deploy --target dev -y`.
- [ ] Run the rebuilt `seed_hesta_members.py` against the new stack.

**✅ Validation checkpoint C — the most important one:**
- Re-run the exact same `describe-stacks`/`list-stack-resources` commands against `AgentCore-ClaimsAgent-dev` (the OLD stack) and diff against the Phase 2 snapshot — **expect zero differences**. Any difference means the old stack was touched and needs immediate investigation before going further.
- Confirm the new stack `AgentCore-ClaimsAgentV2-dev` reports `CREATE_COMPLETE` and its resource list matches §8's inventory.
- Confirm the previously-colliding resource names (§1) now resolve to different physical names/ARNs between the two stacks.
- Run the updated `test_e2e.py`/`test_invoke.py` (§4.4) against the new stack.
- Run 2–3 sample emails through the new runtime: one with a seeded member (full pipeline), one deliberately unseeded (reduced path → human review).
- **Memory recall check (moved here from Checkpoint A — this is the only place it's actually testable):** send two emails from the *same* seeded member a few seconds apart, and confirm the second run's Context Manager output shows recall of the first, via the now-live `ClaimsAgentMemory` resource.

### Phase 4 — Evaluate before treating V2 as ready
- [ ] Confirm `datasets/hesta_eval.jsonl` still matches the new pipeline's tools/outputs.
- [ ] Run the batch-evaluation flow (`OnDemandEvaluationDatasetRunner`) against the new `hestaclaimsagent_v2` runtime.
- [ ] Compare V2's scores against the archived old-stack results in `agentcore/.cli/jobs/batch-eval-results/`.

**✅ Validation checkpoint D:**
- Confirm the OLD stack's own evaluator/online-eval config is still reporting independently (unaffected).

### Phase 5 — Cutover / cleanup
Out of scope for this plan (decision 7/1b) — the old stack's long-term fate is not decided here. The only action item is the standing rule: never touch it. If/when a teardown is ever wanted, use `./scripts/destroy.sh` only, never manual console deletes, and confirm the target name first.

## 6. Open questions

None remaining. Both prior open items are resolved: the old-stack "end state" question is out of scope (decision 7/1b — only rule is "don't touch it"), and the POC-vs-full-project scope boundary is decided (decision 12).

## 7. Documentation refresh — last step (do after everything above)

- [ ] `README.md` — replace the old 6-Lambda/insurance-claims description with the HESTA pipeline.
- [ ] `AGENTS.md` — same; this is the AI-assistant entry point and is currently the most actively misleading.
- [ ] `docs/ARCHITECTURE.md`, `docs/deployment.md`, `docs/tutorial.md`, `docs/CONFIGURATION.md` — update tool/table names, drop the Cedar $100k example (or replace with whatever, if anything, decision 2 changes).
- [ ] `app/hesta-claimsagent/IMPLEMENTATION_PLAN.md` — update §0/§6.1/§8 to reflect that the "post-pilot graduation" has already shipped, or supersede with a new ADR under `docs/decisions/` (next number after 0014).
- [ ] Document §4's pipeline decisions (identity gate, attachment-as-input, seeding strategy, test-script updates) — new behavior, not in the original `IMPLEMENTATION_PLAN.md` at all.

## 8. Full AWS resource inventory (for reference)

Everything this app creates, once deployed as `ClaimsAgentV2` / target `dev`:

| Category | Resource | Notes |
|---|---|---|
| AgentCore | Runtime `hestaclaimsagent_v2` | Container, `app/hesta-claimsagent` |
| AgentCore | Memory `ClaimsAgentMemory` | SEMANTIC + SUMMARIZATION strategies |
| AgentCore | Gateway `ClaimsGateway` | CUSTOM_JWT (Cognito), semantic tool search on |
| AgentCore | Gateway target `member-lookup` | → Lambda `ClaimsAgent-MemberLookup` |
| AgentCore | Gateway target `case-lookup-creation` | → Lambda `ClaimsAgent-CaseLookupCreation` |
| AgentCore | Gateway target `email-review` | → Lambda `ClaimsAgent-EmailReview` |
| AgentCore | PolicyEngine `ClaimsPolicyEngine` | 1 policy: `ClaimsAllowAllTools` (permit-all) |
| AgentCore | Evaluator `ClaimsQualityEvaluator` | LLM-as-judge, SESSION level |
| AgentCore | OnlineEvalConfig `ClaimsEvaluation` | Builtin.Helpfulness / Correctness / ToolSelectionAccuracy, 100% sampling — ⚠️ needs the `agent` field fixed (B3) |
| AgentCore | Dataset `hesta_eval` | from `agentcore/datasets/hesta_eval.jsonl` |
| AgentCore | Credential `cognito-gateway-m2m` | OAuth2 provider, Identity vault |
| DynamoDB | `Hesta-members-dev` | PK `member_id`; GSI `email-index` (PK `email`) |
| DynamoDB | `Hesta-cases-dev` | PK `case_id`; GSI `member_id-index` (PK `member_id`) |
| DynamoDB | `Hesta-humanreview-dev` | PK `review_id`; no GSI |
| Lambda | `ClaimsAgent-MemberLookup` | reads `Hesta-members-dev` |
| Lambda | `ClaimsAgent-CaseLookupCreation` | read/write `Hesta-cases-dev` |
| Lambda | `ClaimsAgent-EmailReview` | read/write `Hesta-humanreview-dev` |
| Lambda | `ClaimsAgent-Trigger` (→ renamed per §1) | S3 → EventBridge → Runtime invoke |
| S3 | Inbox bucket (→ renamed per §1) | Email inbox bucket, EventBridge-enabled |
| ~~SNS~~ | ~~`ClaimsAgent-HumanReview`~~ | **Removed (decision 13)** — not created at all; nothing used it. |
| SQS | Trigger DLQ (→ renamed per §1) | Trigger Lambda's dead-letter queue |
| CloudWatch | Alarm (→ renamed per §1) | On the DLQ's message count |
| EventBridge | Rule (→ renamed per §1) | S3 PutObject (prefix `claims-inbox/`) → Trigger Lambda |
| Bedrock | Guardrail (→ renamed per §1) | Denies personal financial advice topic |
| Cognito | New, separate User Pool + M2M client for `ClaimsAgentV2` (decision 14) | Script-managed (`setup_cognito.sh`), outside CDK — not shared with the old stack's pool |
