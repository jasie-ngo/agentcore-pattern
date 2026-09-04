# Hesta Claims-Agent System — Technical Specification

**Stage:** Articulate (2 of 4: design → articulate → assess → build)
**Source:** `docs/hesta-claims-agent-design-decisions-log.md`
**Status:** Reconciled 2026-09-04 against the real implementation at `02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/app/hesta-claimsagent/` and its governing `IMPLEMENTATION_PLAN.md`.

---

## 1. Purpose & Scope

This specification describes **post-pilot graduation work** for the Hesta claims-agent pilot — a working, hard-coded multi-agent pipeline running on Amazon Bedrock AgentCore, already implemented and governed by its own `IMPLEMENTATION_PLAN.md`. It is not replacing a placeholder; it is extending a real pilot that already performs deterministic identity verification (`identity_profiling.py`), attachment-count-vs-expected checking (`attachment_validation.py`), cost-based model routing (`config.py`), and real AgentCore Memory (`memory/session.py`). "Hardening" here means: extending real verification logic to the post-pilot scope the plan itself already names (Section 3.1); naming and closing a real capability gap the pilot doesn't yet have (attachment content ingestion, Section 3.2); adding genuinely new production mechanisms the pilot deliberately doesn't build (resume-after-approval, Section 3.3); and making deliberate, documented calls on integration, evaluation, and deployment mechanics not addressed by the pilot at all (Glue vs. Lambda, batch evaluation, DevOps gate, model-configuration override). It does not redesign the orchestration paradigm — the agent sequence remains hard-coded — and it does not implement multi-client support, which is explicitly deferred (Section 6).

This document translates the 10 decisions recorded in `docs/hesta-claims-agent-design-decisions-log.md` into concrete component specifications suitable for the assess stage to review and the build stage to implement against. It does not introduce new decisions; where a decision's detail needs elaboration to be buildable, that elaboration is scoped strictly within what was agreed.

**In scope:** identity verification, attachment verification/classification, resume-after-approval, integration layer (Lambda/Glue), batch evaluation, DevOps evaluation gate, memory, observability, model configuration.

**Out of scope:** multi-client support (Section 6); any move to declarative/config-driven workflow orchestration (belongs to the harness-oriented track); replacing Bedrock Guardrails' existing blocked-phrase mechanism.

## 2. System Overview

The real pilot (`app/hesta-claimsagent`) executes a fixed sequence of agents per inbound "email" (any file dropped in the S3 inbox), per `IMPLEMENTATION_PLAN.md` §5:

1. **Normalisation** — deterministic, inside the agent entrypoint — raw email/contact-form content parsed into a canonical `InboundEmail` envelope.
2. **AI-001 Intent Identifier + Attachment Detection** — classifies member intent(s) (multi-intent aware) and detects attachment markers.
3. **AI-002 Conversation Context Manager** — reconstructs the thread, produces a case summary, tracks conversation state (e.g. `awaiting_identity_verification`).
4. **AI-003 Identity & Profiling** — real, working deterministic check today: reuses `lookup_policy`/`PoliciesTable`, producing `verification_level` (`verified`/`partial`/`unverified`) from member number + sender-email match + active status. Extended to post-pilot scope in Section 3.1.
5. **AI-004 Attachment Validation** — real, working today, but pilot-scoped to marker/count comparison only (no file bytes). Extended in Section 3.2, which names a real prerequisite gap.
6. **AI-005 Empathy** — detects vulnerability/complaint/sentiment/priority.
7. **Routing gate** (`routing.py`) — deterministic, no LLM: escalates to human review on regulated intent, unverified identity, low confidence, multiple intents, or empathy flags. **Does not currently read attachment status at all** — fixed in Section 3.2.
8. **AI-011 Writer** — drafts a HESTA-voice reply, **displayed as agent output, never sent**.
9. **AI-012 Reviewer & Editor** — validates the draft's accuracy, tone, and compliance before display.
10. **Human-in-the-loop** — escalated/regulated cases have a record written to DynamoDB via MCP Gateway tools (`request_human_review`/`create_claim`); a human reads the record and the displayed draft **out-of-band** today, with no automated resume mechanism (added in Section 3.3). The system never emails a client directly.

Ingestion is event-driven: a new file lands in S3, which triggers the pipeline via S3 → EventBridge → Trigger Lambda (unchanged, confirmed working). Cost-based model routing already exists (`config.py`: `AGENT_MODEL_ID` for reasoning/writing agents, `FAST_MODEL_ID` for classification-style agents), extended in Section 3.9. AgentCore Memory already exists, actor-keyed on member number with SEMANTIC + SUMMARIZATION strategies (`memory/session.py`), extended in Section 3.7. OTEL tracing is already enabled and reused, confirming the Runtime-hosting assumption this specification originally needed to verify (Section 3.8).

**IMPLEMENTATION_PLAN.md's own governing constraint** for the pilot: "No AWS infrastructure is created or altered — the pilot changes only application code." Several sections below (3.3 resume mechanism, 3.2's attachment-manifest table once its prerequisite lands) are new infrastructure and should be understood as **post-pilot graduation work**, sequenced after or alongside the pilot rather than folded into it silently.

## 3. Component Specifications

### 3.1 Identity Verification

**Real today** (`agents/identity_profiling.py`): reuses `lookup_policy`/`PoliciesTable`, a single-key lookup by member/policy number, producing `verification_level` (`verified`/`partial`/`unverified`) from member-number match + sender-email match + active status. This section specifies the **post-pilot graduation** already named in `IMPLEMENTATION_PLAN.md` §6.1 — it does not replace a placeholder.

**Input:** member number (optional), sender email, and — for the new fallback path — full name and date of birth, extracted by AI-001/AI-002 from the inbound email.

**Lookup paths, in priority order:**
1. **Member number** — exact key lookup against the (renamed, per §6.1) `MembersTable`. Unique by construction; cannot return multiple candidates.
2. **Email** — lookup via a new `email-index` GSI. Unique by construction in the common case; cannot return multiple candidates under normal data.
3. **Name + DOB** — combined lookup with no unique key. **This is the only path that can genuinely return more than one candidate record.**

**Outcomes** (written to the case record as `verification_level`):
- `verified` — a unique record resolved via any path, and cross-check factors (sender email match, active status) hold. Proceed to attachment validation (3.2).
- `partial` — a unique record resolved, but a cross-check factor doesn't hold (e.g. active status fails, or email doesn't match on a name+DOB resolution). Triggers the existing identity-verification sub-flow (conversation state `awaiting_identity_verification`) rather than a hard block.
- `unverified` — no record found on any path.
- `ambiguous` — **name+DOB path only**: more than one candidate record matched. Routes to human review, flagged distinctly from `unverified` so staff know it's a multiple-candidate situation, not a clear non-match. **This outcome must not be designed as reachable from the member-number or email paths — both are unique-key lookups and cannot produce it.**

**Data source:** real today via `PoliciesTable`; the post-pilot graduation (`MembersTable` rename, `email-index` GSI, name+DOB lookup) is scoped work, not a mock-to-real swap — the matching-logic interface should be designed so the eventual real external member-account source (if different from `MembersTable`) can sit behind the same three lookup paths.

### 3.2 Attachment Verification & Classification

**Real today** (`agents/attachment_validation.py`): pilot-scoped to **markers only** — no file bytes are ingested, only `[FILENAME]` presence markers and a count. The check compares attachment count against a single expected-document string per intent (`intents/taxonomy.expected_attachment()`). **`routing.py`'s `decide()` does not take the attachment assessment as input at all** — an `AttachmentAssessment` result currently has zero effect on escalation to human review.

**Named prerequisite (not yet scoped anywhere):** real document content — actual file bytes for each attachment — is not ingested by the pipeline today. Nothing in `IMPLEMENTATION_PLAN.md` scopes this; it must be built before any content-based classification (Bedrock or otherwise) is possible. This specification does not design that ingestion mechanism — it is a dependency to be raised and scoped separately before Section 3.2's classification design can be built.

**Immediate fix, independent of the above:** extend `routing.decide()` to accept the attachment assessment as an input, alongside `intent_result`, `profile`, and `empathy`, and escalate to human review when the assessment indicates a missing or invalid required document — mirroring how `profile.verification_required` and empathy flags already drive escalation today. This closes a real gap and does not depend on the content-ingestion prerequisite.

**Once attachment content ingestion exists (future, separately-scoped work):**
- **Manifest:** a DynamoDB table (`AttachmentManifest`), keyed by case type, generalising the current single-expected-document-per-intent taxonomy lookup into required/optional document categories and acceptable file types per case type.
- **Classification:** a Bedrock-based classification step scores each attachment's real content against the manifest's category list, returning a category and confidence per attachment.
- **Outcomes** (written to the case record as `attachment_status`): `complete` (proceed), `incomplete` (missing required category — human review, draft request-for-documents reply), `unrecognised` (content present but doesn't match expected categories — human review).

### 3.3 Resume-After-Approval Mechanism

**Real today:** human-in-the-loop is a DynamoDB record written via MCP Gateway tools (`request_human_review`/`create_claim`), read **out-of-band** by a human — a deliberate, working pilot design, not a gap in the pilot. `IMPLEMENTATION_PLAN.md` §0 states explicitly: "No AWS infrastructure is created or altered [in the pilot]." Everything below is **new infrastructure beyond the pilot's stated scope** — it should be planned and communicated as an addition, not folded into pilot delivery.

**Trigger:** the review UI writes a status update to the case's DynamoDB item (`status: APPROVED | DECLINED`, plus reviewer identity and timestamp). This write enables a **DynamoDB Streams** trigger on the case table.

**Dispatcher:** a Lambda subscribed to the stream reads the new status and the session ID already stored on the case item, then invokes the appropriate next step:
- `APPROVED` → hand off to the outbound-send step (drafts go to Hesta staff's outbox for sending; the system does not email clients directly, per the existing constraint).
- `DECLINED` → case-closure/notification step, no outbound draft sent.

The dispatcher Lambda contains **no business logic** beyond this status-to-action mapping — decision logic stays inside the agent pipeline it invokes, keeping it testable and observable alongside the rest of the pipeline.

**Idempotency:** DynamoDB Streams can redeliver events. The case item carries a `resumedAt` timestamp, set atomically on first successful dispatch; the Lambda checks this before acting, so a redelivered stream record is a no-op.

**This mechanism reuses the session ID already established by the context/session agent in step 3** — no new session-management concept is introduced.

### 3.4 Integration Layer (Lambda-only)

All integration and job-processing work in this system uses **Lambda**; Glue is not introduced.

**Event sources by job:**
- S3 object-created event → email-ingestion Lambda (existing, unchanged).
- DynamoDB Streams on the case table → resume-after-approval dispatcher Lambda (Section 3.3).
- EventBridge schedule → batch-evaluation dataset refresh Lambda (Section 3.5).
- Per-case external lookups (identity/member-account source, Section 3.1) → invoked synchronously within the identity-verification agent's Lambda.

**Scaling seams to leave open, not build now:**
- An SQS (or EventBridge) buffer between the S3 ingestion event and the processing Lambda, to absorb bursts without hitting Lambda concurrency limits or Bedrock throttling. Not required at current volume; add when burst behaviour is observed.
- Step Functions to chain Lambdas if any job (e.g. the eval-dataset refresh) exceeds a single invocation's time limit. This orchestrates infrastructure jobs only — it does not sequence agents and does not reintroduce declarative agent-workflow orchestration.
- DynamoDB on-demand capacity mode, to avoid manual capacity planning as case volume grows.

**Documented Glue-reconsideration trigger:** revisit Glue only if the batch-evaluation export exceeds a size where a Lambda-based transform routinely approaches its timeout, or the eval dataset outgrows what a single Lambda invocation can process even when chained via Step Functions. This is a deliberate future decision point, not a default.

### 3.5 Batch Evaluation

**Real today:** `IMPLEMENTATION_PLAN.md` §9/Phase 0 already establishes a working eval fixture set — the 23 de-identified email samples (`hesta/{BDBN,BP,COD,DASP,FH,FLS,NOI,RTC}/`), folder name as ground-truth intent label, used to measure intent precision/recall.

**Dataset:** **extend the existing 23-sample fixture set**, rather than introducing a separate dataset. Add richer expected-outcome fields to each sample, alongside the intent label it already carries:
- expected `verification_level` (3.1)
- expected `attachment_status` and category classifications (3.2, once its content-ingestion prerequisite lands)
- expected final draft content (key facts/phrases, not exact-match text)
- expected guardrail behaviour (should this trigger a blocked phrase or not)

**Dataset build/refresh:** an EventBridge-scheduled Lambda reads a DynamoDB point-in-time export of case history from S3 and transforms it into fixture-shaped entries, additive to the 23 curated samples, not a replacement for them.

**Execution:** batch evaluation invokes the **same production agent pipeline code**, tagged with an **eval-mode flag** and a dedicated case-number namespace (e.g. `EVAL-` prefix). This ensures:
- results never appear in the live human-review UI queue;
- results never write to the production case table;
- the pipeline logic under test is the real one, not a reimplementation.

**Scoring:** each sample's actual output is graded against its expected-outcome record via a Bedrock model-as-judge, producing per-sample scores for coherence, correctness, helpfulness, and refusal-correctness. Results are written to a separate eval-results store (S3 or a dedicated DynamoDB table), isolated from production case data and from the in-flight evaluation's storage.

**Isolation from in-flight evaluation:** the two evaluation surfaces differ on trigger (on-demand/scheduled batch run vs. inline on every real case), data path (curated dataset vs. real inbound emails), and storage (separate eval-results location vs. whatever the in-flight evaluator already writes to). No shared state between them.

### 3.6 DevOps Evaluation Gate

**Placement:** triggered on merge to the main branch, between build and deploy. A candidate build (potentially across multiple model families, per Section 3.9) is evaluated via the batch-evaluation harness (3.5) before deployment proceeds. Failure blocks deployment; there is no automatic rollback or retry — a failure always routes to manual review.

**Threshold policy, per metric:**

| Metric | Method | Pass condition |
|---|---|---|
| Refusal correctness | Direct inspection of the McNemar disagreement table between candidate and current production baseline, on the guardrail-trigger subset of the eval dataset | Zero regressions: candidate must not fail any guardrail case the baseline passed. No significance test — any observed regression blocks, regardless of sample size. |
| Coherence / correctness / helpfulness | Paired per-query comparison (Wilcoxon signed-rank on candidate-minus-baseline score deltas), reported as effect size + bootstrap 95% CI, framed as one-sided non-inferiority | No statistically significant **and** operationally meaningful regression (pre-registered minimum delta per metric). If the CI is too wide to distinguish signal from noise given current dataset size, the outcome is `INCONCLUSIVE`, not a pass. |

**Multi-model correction:** when testing 3+ candidate model families in one gate run (per Section 3.9), a Holm-Bonferroni correction is applied across all candidate-vs-baseline pairs before any pass/fail determination, to control the family-wise false-pass rate.

**Outcomes:** `PASS` (deploy proceeds), `FAIL` (regression detected, deploy blocked, routed to manual review with the specific metric/evidence attached), `INCONCLUSIVE` (dataset underpowered for a graded metric, deploy blocked, routed to manual review flagged as a signal to grow the eval dataset in that area — see Section 7).

### 3.7 Memory

**Real today** (`memory/session.py`): AgentCore Memory already exists, actor-keyed on member number, with **SEMANTIC** (`claims/{actorId}/facts`) and **SUMMARIZATION** (`claims/{actorId}/{sessionId}`) strategies, per ADR `0008-semantic-summarization-memory.md`. No **user-preference** strategy is currently configured. `IMPLEMENTATION_PLAN.md` is internally inconsistent on the correction-feedback loop: §2's table says corrections go to "Memory — no new build," while §8 Phase 5 says "Memory **+ a feedback store**." This specification resolves that inconsistency in favour of the "reuse Memory, no new build" reading, since it is stated first and matches the plan's dominant philosophy throughout.

**A) Per-member personalisation memory.** Add a **user-preference** strategy to the existing Memory resource (a configuration change to what's already deployed — not a new resource). No change to the actor-keying already in place.

Episodic history (this member's past cases) continues to come from the case/claims table directly — no change needed here.

**B) Staff-correction feedback loop.** Uses the **same existing Memory resource**, with a **case-type pseudo-actor-id** (e.g. `casetype:FH`) instead of a member actor:

- **Write path:** the resume-after-approval mechanism (3.3), at the point it processes an approved or edited-and-approved case, diffs the agent's original draft against the staff-edited final version and writes it as a memory event via the same mechanism `memory/session.py`'s `record_interaction()` already implements, scoped to the case-type pseudo-actor rather than a member actor.
- **Read path:** the Writer and Reviewer/Editor agents (AI-011/AI-012) retrieve recent entries for the case's case-type pseudo-actor when composing/reviewing a draft.
- **Escalation:** if the same correction pattern recurs across multiple case types, this is a signal for a periodic (manual, not automated) review to consider promoting it to a company-wide pseudo-actor.

**No new DynamoDB table is introduced for this.** A dedicated `CorrectionMemory` table was considered and rejected: it isn't clearly authorised by `IMPLEMENTATION_PLAN.md` and conflicts with the "no new build" statement in the section of that document that states it most strongly. Reusing the actor-scoping mechanism already built — with a case-type pseudo-actor instead of a member actor — achieves the same case-type granularity without new infrastructure. **This resolution should be confirmed with whoever owns `IMPLEMENTATION_PLAN.md` before build**, since reconciling the plan's internal inconsistency was this specification's judgment call, not a decision the plan's author explicitly made.

### 3.8 Observability

**Mechanism:** rely on AgentCore's native OpenTelemetry (OTEL) tracing to Amazon CloudWatch GenAI Observability, correlated by the session ID already stored per case (3.3). No bespoke per-agent logging table is built.

**Precondition (verify before build):** automatic OTEL instrumentation applies when agents are hosted on **AgentCore Runtime**. This must be confirmed against the actual repository; if any agent runs outside Runtime, it requires manual instrumentation via the AWS Distro for OpenTelemetry (ADOT) SDK.

**Required addition — outcome tagging:** native spans capture tool calls, timing, and token usage, but not domain-specific decision outcomes. Each agent must be explicitly modified to attach a custom span attribute recording its decision outcome (e.g. `identityStatus = VERIFIED`, `attachmentStatus = INCOMPLETE`). This is deliberate per-agent work, not a byproduct of Runtime hosting.

**Review-UI surface:** a condensed step list — agent name, outcome label, timestamp — is written to the case record **once, when each agent completes** (not queried live from CloudWatch on UI load, to avoid added latency and API cost). The review UI renders this list alongside the draft. A "view full trace" link out to the CloudWatch console provides full span detail for engineers who need it.

**PII scoping:** traces may contain claim content. IAM policy and data-handling on the trace-query path must explicitly restrict access; this is not safe by default and must be designed as part of the build stage (Section 7).

### 3.9 Model Configuration

**Real today** (`config.py`): "ALL env var reads live here — nowhere else." `AGENT_MODEL_ID` and `FAST_MODEL_ID` already implement cost-based routing (strong model for AI-002/AI-011/AI-012, fast/cheap model for AI-001/AI-004/AI-005 classification-style tasks), per ADR `0013-cost-routing-fast-model-for-validator.md`. Changing a model today means a redeploy — exactly the problem this decision targets.

**Mechanism:** add a DynamoDB-backed override layer **inside `config.py`**, not beside it — preserving its stated role as the single source of truth for every model-ID read in the codebase. Per-agent settings entry, shaped as:

```json
{
  "agent": "empathy-agent",
  "primaryModelId": "anthropic.claude-...-v1:0",
  "canaryModelId": "anthropic.claude-...-v1:0",
  "canaryPercent": 20
}
```

At invocation, `config.py` performs a single weighted random draw (or a deterministic case-ID hash, for reproducible bucketing across a case's retries) against this config to select which model to return. This is the only change made to model-invocation code — the agent sequence itself is untouched, and no second, competing configuration mechanism is introduced.

**Rejected alternative:** Bedrock Intelligent Prompt Routing was evaluated and does not fit — it performs automatic quality/cost-based routing within a single model family with no manual percentage control, and cannot express a fixed "route 20% of traffic to a specific candidate model" experiment.

**Attribution:** each case record stores which model variant served it. Combined with the observability trace (3.8) and the DevOps gate's paired-comparison framework (3.6), this supports evaluating a canary model's real-traffic performance, not just its batch-eval performance.

## 4. Data Model Changes

| Table / Index | Change | Purpose | Section |
|---|---|---|---|
| `PoliciesTable` → renamed `MembersTable` | Rename only, per `IMPLEMENTATION_PLAN.md` §6.1; no schema change to existing fields | Post-pilot identity graduation | 3.1 |
| `MembersTable` | Add `email-index` GSI | Email-based identity lookup path | 3.1 |
| Claims/Reviews tables | Add `resumedAt` field | Idempotency guard on Streams redelivery | 3.3 |
| Claims/Reviews tables | Add `observability_trace` field (condensed step list) | Review-UI display without live CloudWatch query | 3.8 |
| `AttachmentManifest` (new, **depends on attachment-content-ingestion prerequisite**) | Keyed by case type; required/optional document categories, generalising `intents/taxonomy.expected_attachment()` | Replaces single-expected-document-per-intent taxonomy lookup | 3.2 |
| Model-config settings (new, read by `config.py`) | Keyed by agent name; primary/canary model IDs + canary percentage | Config-driven model selection, extends existing `AGENT_MODEL_ID`/`FAST_MODEL_ID` pattern | 3.9 |
| Eval-results store (new, S3 or DynamoDB) | Batch-eval scores per sample, per gate run | Isolated from production case data | 3.5, 3.6 |
| Existing AgentCore Memory resource | Add **user-preference** strategy (SEMANTIC + SUMMARIZATION already configured) | Per-member personalisation | 3.7A |
| Existing AgentCore Memory resource | New case-type pseudo-actor-ids (e.g. `casetype:FH`), same resource, same event-write mechanism as `record_interaction()` | Staff-correction feedback loop — no new table | 3.7B |

No new DynamoDB table is required for the correction-feedback loop (3.7B) — it reuses the existing Memory resource. `AttachmentManifest` depends on the attachment-content-ingestion prerequisite named in 3.2 and should not be built before that prerequisite is scoped and delivered.

## 5. Non-Functional Requirements

- **Auditability:** identity verification (3.1) and attachment classification (3.2) must produce deterministic, explainable outcomes — no confidence-scored or black-box decision may result in `VERIFIED`/`COMPLETE` without a human-inspectable basis, given the compliance sensitivity of super-fund member data.
- **No direct client communication:** preserved unchanged — all outbound drafts require Hesta staff approval via the review UI before sending; this specification does not alter that boundary anywhere, including in the resume mechanism (3.3).
- **PII handling:** case content (including attachments and observability traces, 3.8) must not be more widely accessible than the current case-table access pattern; trace data delivered to CloudWatch requires explicit IAM scoping, not default access.
- **Latency:** the review-UI observability surface (3.8) must not introduce live external API calls on page load — the condensed step list is pre-written to the case record.
- **Deploy safety:** no deployment proceeds without passing the DevOps evaluation gate (3.6); failures and inconclusive results always route to manual review, never an automatic retry or rollback.
- **Isolation:** batch-evaluation runs (3.5) must never write to production case data or appear in the live human-review queue, under any failure mode of the eval-mode flag/namespace mechanism.

## 6. Out of Scope

**Multi-client support is explicitly out of scope for this specification.** The current design — guardrail phrases, the identity-source integration (3.1), agent prompts (empathy/case-study/editing), and every DynamoDB table introduced or modified in Section 4 — carries no client dimension. Adding a second client under this specification as written would require either full infrastructure duplication or non-trivial retrofitting across Sections 3.1–3.9.

The recommended future path, when multi-client support is prioritised, is **silo-per-client via parameterized IaC** (one CDK/CloudFormation template, deployed as a separate stack per client) rather than a shared-table "pool" model with a `client_id` partition dimension — this matches AWS's own multi-tenant SaaS guidance for compliance-heavy, small-tenant-count scenarios, and avoids the pool model's tenant-lookup/sharding complexity. This path is noted here for future reference only; no work toward it is included in this specification.

## 7. Open Risks & Assumptions to Verify Before Build

| # | Risk / Assumption | Section | Verification needed before build |
|---|---|---|---|
| 1 | ~~Pipeline agents are hosted on AgentCore Runtime~~ **RESOLVED** — confirmed real; OTEL traces/metrics are already enabled and reused per `IMPLEMENTATION_PLAN.md` | 3.8 | None — confirmed against the real repository. |
| 2 | AgentCore Memory built-in strategies (semantic, user-preference) produce useful extraction quality for this domain's conversational content | 3.7A | SEMANTIC/SUMMARIZATION are already real and working; user-preference is net-new — trial against representative case emails before committing to it |
| 3 | Observability traces may carry PII (claim content) into CloudWatch | 3.8 | IAM/data-handling scoping must be explicitly designed, not assumed safe by default — still open |
| 4 | Batch-evaluation dataset (3.5) will start small (23 fixture samples plus growth) | 3.6 | Expect `INCONCLUSIVE` gate outcomes early on for graded metrics — this is intended behaviour, not a defect, and should not be worked around by loosening the statistical test |
| 5 | **Named prerequisite, not yet scoped:** attachment content ingestion (real file bytes) doesn't exist in the pilot — Section 3.2's Bedrock classification design cannot be built until this lands | 3.2 | Scope and estimate the ingestion mechanism as its own piece of work before committing to a Section 3.2 build date |
| 6 | Post-pilot identity graduation (`MembersTable` rename, `email-index` GSI, name+DOB lookup) is scoped in `IMPLEMENTATION_PLAN.md` §6.1 but not yet built or scheduled | 3.1 | Confirm sequencing with whoever owns the implementation plan; this specification assumes it lands before or alongside Section 3.1's build |
| 7 | `IMPLEMENTATION_PLAN.md`'s internal inconsistency on the correction-feedback loop (Memory-only vs. Memory+feedback-store) was resolved by this specification in favour of Memory-only | 3.7B | Confirm this resolution with the plan's owner before build — it was this specification's judgment call, not an explicit decision in the source document |
| 8 | `routing.py`'s `decide()` does not currently accept an attachment assessment at all | 3.2 | Confirm the routing-gate fix is scheduled independently of the content-ingestion prerequisite — it does not need to wait on it |

## 8. Appendix: Traceability to Decisions Log

| Spec section | Decisions log item |
|---|---|
| 3.1 Identity Verification | 1 |
| 3.2 Attachment Verification & Classification | 2 |
| 3.3 Resume-After-Approval Mechanism | 3 |
| 3.4 Integration Layer | 4 |
| 3.5 Batch Evaluation | 5 |
| 3.6 DevOps Evaluation Gate | 6 |
| 3.7 Memory | 7 |
| 3.8 Observability | 8 |
| 3.9 Model Configuration | 9 |
| 6 Out of Scope | 10 |
