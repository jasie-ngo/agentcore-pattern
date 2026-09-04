# Hesta Claims-Agent System — Design Decisions Log

**Stage:** Design (1 of 4: design → articulate → assess → build)
**Date:** 2026-09-03 (reconciled against real repository 2026-09-04)
**Scope:** Hardening the existing hard-coded multi-agent claims pipeline. Orchestration sequence is preserved as-is; declarative/config-driven workflow definitions are explicitly out of scope for this track (belongs to the harness-oriented track).
**Constraints:** AWS-native only (Bedrock AgentCore, Bedrock Guardrails, DynamoDB, S3, Lambda). Extend the existing repository, don't replace it.

## Reconciliation note (added 2026-09-04)

This log was originally written from a verbal description of the system's state. That description does not match the real repository: the actual pilot implementation and its governing plan already exist at `02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/app/hesta-claimsagent/` (see `IMPLEMENTATION_PLAN.md`, status "DRAFT for review," and the working agent code under `agents/`). The real pilot already implements deterministic identity verification (not a placeholder), attachment-count-vs-expected checking (not filename matching), real cost-based model routing, and real AgentCore Memory. Every decision below has been reconciled against that real plan and code. Decisions are now framed explicitly as **post-pilot graduation work** — extending a real, working pilot — rather than replacing a "placeholder." Two design flaws introduced by the original (ungrounded) framing have been corrected: an identity outcome that couldn't occur against the real lookup mechanism (Decision 1), and an attachment-classification design that assumed file content the pilot doesn't ingest (Decision 2).

---

## 1. Identity verification

**Grounding:** `agents/identity_profiling.py` reuses the existing `lookup_policy` Gateway tool + `PoliciesTable` (single-key lookup by member/policy number), producing a `verification_level` of `verified` / `partial` / `unverified` based on member-number match + sender-email match + active status. `IMPLEMENTATION_PLAN.md` §6.1 names the post-pilot graduation explicitly: rename to `lookup_member`, add a `MembersTable`, add an `email-index` GSI so a lookup can also resolve by email, and support a `name+dob` fallback path when no member number or email match is available.

**Decision:** Build the post-pilot graduation exactly as scoped in §6.1 — `lookup_member` with three resolution paths (member number exact match → email-index GSI → name+DOB combined lookup) — and extend, rather than replace, the real three-level outcome model.

- Member number or email match against a unique key → **verified** if the cross-check factors (sender email, active status) also hold, **partial** otherwise (mirrors today's logic exactly).
- Name+DOB fallback (the one lookup path with no unique key) can genuinely return more than one candidate record — this is the **only** path where an `ambiguous` outcome is real. Member-number and email lookups, being unique-key lookups, cannot produce it and must not be designed as if they could.
- **unverified**: no record found on any path.
- `ambiguous`, `partial`, and `unverified` all route to human review (or the existing identity-verification sub-flow the samples already show HESTA uses); `ambiguous` is flagged distinctly so staff know it's a data-quality/multiple-candidate situation, not a clear mismatch.

**Reasoning:** The original design's `AMBIGUOUS` outcome was invented against an imagined "exact multi-field match" mechanism that doesn't exist and isn't planned even post-pilot for the member-number/email paths (both are unique-key lookups by construction). Anchoring the outcome model to the real, plan-specified lookup paths keeps this decision buildable and avoids designing a state the code can never produce. A false "verified" remains the compliance risk this guards against; bias stays toward routing to human review over scoring/leniency, consistent with the pilot's existing "never act on an unmatched/unverified member" posture.

---

## 2. Attachment verification

**Grounding:** `agents/attachment_validation.py` is explicitly pilot-scoped to **markers only** — "attachments arrive only as markers (no file bytes)... Real document parsing (type, completeness, legibility) is post-pilot." It compares an attachment *count* against a single expected-document string per intent (`intents/taxonomy.expected_attachment()`). Separately, `routing.py`'s `decide()` takes `intent_result`, `profile`, and `empathy` — it does **not** take the attachment assessment as input at all, so today an `AttachmentAssessment` result cannot influence escalation to human review regardless of what it says.

**Decision:** This decision has a **named prerequisite that must be built first and is not yet scoped anywhere**: real attachment content ingestion (fetching actual file bytes, not just `[FILENAME]` markers, into the pipeline). Bedrock-based content classification has nothing to classify against until that exists. Sequencing:

1. **Prerequisite (new scope):** attachment ingestion — the pipeline must receive actual file bytes for each attachment, not just presence markers. This is genuinely new work, not implied by anything currently planned.
2. **Once file content exists:** replace the current single-expected-document-per-intent taxonomy lookup with a DynamoDB manifest per case type (required/optional document categories), and add a Bedrock-based classification step that scores each attachment's real content against that manifest.
3. **Fix regardless of the above:** `routing.decide()` must be extended to accept the attachment assessment and escalate on `incomplete`/`unrecognised`, the same way it already reads `profile.verification_required` and `empathy` flags. This is a small, immediate fix independent of the content-ingestion prerequisite, and should not wait on it — today a missing required document has no path to forcing human review at all.
- Outcomes once built: **complete** (proceed), **incomplete** (missing required category → human review, draft request-for-documents reply), **unrecognised** (content present but doesn't match expected categories → human review).

**Reasoning:** The original design assumed Bedrock could classify attachment content that the pilot never receives — that's a capability gap, not a design choice, and needs to be named as a dependency rather than silently assumed away. The routing-gate fix is separable and cheap, and closes a real gap (attachment outcome currently has zero effect on escalation) regardless of how far the content-ingestion/classification work progresses.

---

## 3. Resume-after-approval

**Grounding:** `IMPLEMENTATION_PLAN.md` §0 states human-in-the-loop is "exactly as implemented today: write a record to DynamoDB via MCP Gateway (`request_human_review`/`create_claim`) — a human reads the record out-of-band," and states plainly: **"No AWS infrastructure is created or altered [in the pilot]."**

**Decision:** DynamoDB Streams trigger on the case-status table → Lambda dispatcher → resumes/re-invokes the pipeline using the stored session ID, exactly as originally designed — but this is **explicitly new infrastructure beyond the pilot's stated scope**, not a gap in the pilot itself (the pilot's "a human reads the record out-of-band" is a deliberate, working design for pilot purposes, not an oversight).

- Staff action in the review UI (or whatever review surface exists) writes a status update (`APPROVED`/`DECLINED`) to the case record.
- DynamoDB Streams fires on that update; a narrow, business-logic-free Lambda reads the new status + stored session ID and calls back into the appropriate next step.
- Idempotency handled via a `resumedAt`-style marker to guard against Streams redelivery.

**Reasoning:** Automating the hand-off is real, valuable hardening work for production use, but it should be sequenced and communicated as **new infrastructure added on top of a working pilot mechanism**, not a fix to something broken. This affects how the build stage should be scoped and staffed relative to the pilot's own phased delivery plan.

---

## 4. Glue vs. Lambda

**Decision:** **Lambda only.** No Glue. (Not addressed either way by `IMPLEMENTATION_PLAN.md`; this decision stands as originally reasoned.)

- All integration work (event-driven, per-case: S3 ingestion, DynamoDB Streams resume, external lookups, outbound send) uses Lambda.
- The batch-evaluation data pipeline (Decision 5) — the one plausible Glue candidate — instead uses **DynamoDB's native point-in-time export to S3** plus a scheduled (EventBridge-triggered) Lambda for transform, avoiding Glue crawlers/jobs entirely.
- **Scaling seams left open without adding complexity now:** SQS/EventBridge buffering between S3 events and processing Lambdas to absorb bursts; Step Functions to chain Lambdas if a job exceeds one invocation's limits; DynamoDB on-demand capacity; incremental/partitioned exports. An explicit documented trigger ("if export exceeds N GB or Lambda-ETL routinely times out") is the deliberate point to revisit Glue later.

**Reasoning:** Unchanged from the original reasoning — current and near-term data volumes don't justify Glue's distributed-processing value proposition.

---

## 5. Batch evaluation

**Grounding:** A real, working eval fixture set already exists in the plan: the 23 de-identified email samples (`hesta/{BDBN,BP,COD,DASP,FH,FLS,NOI,RTC}/`), used with folder-name-as-ground-truth-label for intent precision/recall (`IMPLEMENTATION_PLAN.md` §9, Phase 0 "Eval fixtures").

**Decision:** **Extend the existing 23-sample fixture set**, rather than introducing a separate, parallel dataset from scratch. Add richer expected-outcome fields to each existing sample (expected `verification_level`, expected attachment outcome once Decision 2's prerequisite lands, expected guardrail behaviour, expected draft key facts) alongside the intent label the fixtures already carry.

- Grows over time via the same mechanism originally designed: a scheduled Lambda transforms a DynamoDB point-in-time export of real case history into additional fixture-shaped entries, additive to the 23 curated samples.
- Runs against the same production pipeline code, tagged with an eval-mode flag and a dedicated case-number namespace, isolated from production case data and the live review queue.
- Scored via Bedrock model-as-judge against each sample's expected-outcome record.

**Reasoning:** Building on the fixture set the team has already validated (folder-labelled, drawn from real HESTA email samples) is both less work and more credible than inventing a new dataset — it inherits ground truth the team has already reviewed rather than asking them to trust a second, parallel artifact.

---

## 6. DevOps evaluation pipeline (deploy gate)

**Decision:** Unchanged from the original design — positioned as a gate between PR merge and deployment, using the extended fixture set from Decision 5.

**Threshold policy — split by metric type:**

- **Refusal correctness:** deterministic, zero-tolerance rule (McNemar disagreement table, direct inspection) — any regression on the guardrail-trigger subset blocks deployment, full stop, regardless of sample size.
- **Coherence / correctness / helpfulness:** paired non-inferiority test (Wilcoxon signed-rank) against the current production baseline, with a third outcome — **inconclusive** — when the dataset is too small to distinguish signal from noise.
- **3+ model families tested at once:** Holm-Bonferroni correction across all candidate-vs-baseline pairs before any pass/fail determination.

**Reasoning:** Unchanged — a flat regression-percentage threshold can't distinguish real regressions from small-sample noise, and the three-outcome model keeps "insufficient data" honest rather than defaulting to a false pass.

---

## 7. Memory

**Grounding:** `memory/session.py` already uses real AgentCore Memory with **SEMANTIC** and **SUMMARIZATION** strategies (per ADR `0008-semantic-summarization-memory.md`), actor-keyed on member number, with namespaces `claims/{actorId}/facts` and `claims/{actorId}/{sessionId}`. No **user-preference** strategy is currently configured. `IMPLEMENTATION_PLAN.md` is internally inconsistent about the staff-correction loop: §2's table says corrections go to "Memory — no new build," while §8 Phase 5 says "Memory **+ a feedback store**."

**Decision:**

**A) Per-member personalisation memory** — mostly already real. Add a **user-preference** strategy to the existing Memory resource (a configuration change to what's already deployed, not a new resource) to capture the kind of durable preference/fact the original design intended. Episodic case history continues to come from the case table directly (no change needed here).

**B) Staff-correction feedback loop** — resolve the plan's internal inconsistency by favouring the "reuse Memory, no new build" reading, since it's stated first and matches the pilot's dominant philosophy throughout the document. Concretely: write correction diffs as memory events against a **pseudo-actor-id scoped to case type** (e.g. `casetype:FH`), using the same event-write mechanism `memory/session.py`'s `record_interaction()` already implements — extending that existing helper rather than building a new DynamoDB table or new AWS infrastructure. This achieves case-type-scoped correction memory (the granularity rationale from the original design still holds: not company-wide, dilutes signal; not per-staff-user, doesn't generalise) entirely within the Memory resource that already exists.

**Reasoning:** The original design's new `CorrectionMemory` DynamoDB table wasn't clearly authorised by the plan and directly conflicts with its "no new build" framing in the section that states it most strongly. Reusing the actor-scoping mechanism already built (just with a case-type pseudo-actor instead of a member actor) achieves the same case-type granularity without new infrastructure, and resolves — rather than ignores — the plan's own internal contradiction.

---

## 8. Observability

**Grounding:** `IMPLEMENTATION_PLAN.md` confirms OTEL traces/metrics are "already-enabled," reused "no new build" — this **resolves** the open risk (Runtime hosting assumption) from the original spec: the pipeline is confirmed to already emit native traces.

**Decision:** Unchanged in mechanism — rely on AgentCore's native OTEL tracing, correlated by session ID. The outcome-tagging (per-agent decision-outcome span attributes) and the condensed review-UI step list remain genuinely new **application-code** work, not new infrastructure — consistent with the pilot's "the pilot changes only application code" framing, so this is buildable within the pilot's own philosophy even though it isn't in the current plan.

- Each agent must explicitly tag its span with a decision-outcome attribute (e.g. `verification_level = verified`) — deliberate small addition per agent.
- Condensed step list (agent name, outcome, timestamp) written to the case record once per agent completion, not queried live from CloudWatch.
- PII scoping on the trace path remains an open item — must be explicitly designed, not assumed safe by default.

**Reasoning:** With Runtime hosting now confirmed, the only remaining open item from the original design is the PII-scoping question — everything else in this decision stands as application-code work layered onto an already-real capability.

---

## 9. Model configuration

**Grounding:** `config.py` already centralises all model selection via `AGENT_MODEL_ID` and `FAST_MODEL_ID` environment variables (cost-based routing: cheap model for classification-style agents, strong model for summarisation/writing/review), per ADR `0013-cost-routing-fast-model-for-validator.md`. This is real, working, and env-var-driven — changing a model today means a redeploy, exactly the problem the original decision targeted.

**Decision:** Add a DynamoDB-backed override layer **behind `config.py`'s existing centralisation pattern**, rather than inventing a new config path — `config.py`'s own docstring states "ALL env var reads live here — nowhere else," so the DynamoDB lookup should be added as a fallback/override read inside this same module, preserving it as the single source of truth for every model-ID read in the codebase. Shape: per-agent primary/canary model ID + canary percentage, with a one-line weighted draw at invocation.

**Reasoning:** This is genuine new work, not a correction — but it must extend the real centralisation pattern the team already built (`config.py`) rather than introduce a second, parallel configuration mechanism, or the codebase ends up with two competing ways to determine a model ID.

---

## 10. Multi-client support

**Decision:** Unchanged — **explicitly out of scope for this hardening track.** (Not addressed either way in `IMPLEMENTATION_PLAN.md`, which is itself single-client/HESTA-specific throughout, consistent with this decision.) When pursued, the recommended path is **silo-per-client via parameterized IaC**, not a shared-table "pool" model.

**Reasoning:** Unchanged from the original decision.

---

## Cross-cutting notes for the articulate stage

- **This log now describes post-pilot graduation work built on a real, working pilot** (`IMPLEMENTATION_PLAN.md`), not replacement of a placeholder — every downstream document (spec, premortem) must carry this framing forward.
- Decision 2 has a named prerequisite (attachment content ingestion) that isn't scoped anywhere yet and should be raised as its own open question before build, not silently assumed.
- Decision 7 resolves an internal inconsistency in the real implementation plan (Memory-only vs. Memory+feedback-store) — this resolution should be confirmed with whoever owns `IMPLEMENTATION_PLAN.md` before build, since it wasn't this team's document to unilaterally reinterpret.
- Decision 9 must land inside `config.py`, not beside it, to avoid two competing model-configuration mechanisms.
- Decision 6's statistical framework depends on Decision 5's dataset reaching sufficient size — expect "inconclusive" gate outcomes early on; this is intended behaviour, not a flaw.
- Decision 10 is the one deliberately unresolved tension per the brief — flagged, not silently designed around.
