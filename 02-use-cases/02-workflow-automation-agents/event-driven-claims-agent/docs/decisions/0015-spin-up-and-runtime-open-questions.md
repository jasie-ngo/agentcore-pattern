# ADR-0015: Spin-up and runtime design: nine open questions, and the revised roadmap

## Status

**Accepted on 2026-10-22: the recommendation for every question is adopted.** What remains **open** are parameters only the
fund or its compliance and security people can supply (named signers, SLA hours, retention period, the preconditions for
"certain", and similar). Each is marked **Open** under its question, with the safe fallback that applies until it is answered.
Work that depends on an open parameter must not start until it is.

## Context

The intended design has two stages.

- **Stage 1 (spin-up):** a client's artefacts and feature files go in `clients/`; an LLM on Amazon Bedrock synthesises them
  into a knowledge graph, guardrails and validation gates; common CDK builds the infrastructure; the result is plugged in
  and deployed.
- **Stage 2 (runtime):** a member emails in; the agent recalls that member's memory, validates the request against the
  knowledge base, guardrails and gates, proceeds if certain, and otherwise involves a human.

A review of that design (AgentCore-focused, run 2026-10-22) found that Stage 2 is mostly sound, and several of its guarantees
are already enforced in code, while Stage 1 carries the largest unexamined risks. The YAML-first pipeline
(`app/04_hesta_member_email`) is now the core. The nine questions below are the ones the design cannot be built or deployed
safely without answering.

**How to read the evidence.** *Verified* means I read it in this repository. *Unverified* means it comes from the review or
from general knowledge and should be checked before you rely on it. The review did not read the CDK stack or the Lambdas, so
anything about tenancy, encryption or residency below is a question, not a finding.

---

## The nine questions

### Q1. Which synthesised artefacts are binding, and who signs each one?

- **Why it matters:** if the same model that writes the guardrails and gates also defines what "safe" means, nothing independent
  checks it. The design as described goes from synthesis to deploy with no approval between.
- **Evidence (verified):** the Cedar pack already needs an approval bound to the hashes of the feature file and the vocabulary,
  and the loader fails closed without it. But approvals are unsigned JSON, and the thresholds, the guardrail topics and any
  knowledge graph have no approval record at all.
- **Options:**
  - **A.** Everything *binding* needs a signed approval; anything *advisory* needs only review. Binding: the Cedar pack, routing
    thresholds and the regulated-intent flags, guardrail topics, the identity-verification policy. Advisory: reply snippets,
    taxonomy descriptions, prompt wording. The LLM may only propose binding artefacts as a diff.
  - **B.** Only the Cedar pack is binding. Everything else is advisory.
  - **C.** LLM output goes live once an automated evaluation passes, with no human sign-off.
- **Recommendation: A.** B leaves the thresholds and guardrails, the parts most likely to hurt a member, unreviewed.
  C removes the one control the design currently has.
- **Decision: A adopted.** Binding: the Cedar pack, routing thresholds and regulated-intent flags, guardrail topics, the
  identity-verification policy. Advisory: reply snippets, taxonomy descriptions, prompt wording. The LLM only proposes binding
  artefacts, as a diff.
  **Open:** the named signer, by role, for each regulated intent.

### Q2. What is the labelled evaluation set, and what regression result blocks a deploy?

- **Why it matters:** with no ground truth you cannot tell whether a re-synthesis improved or silently broke the system.
- **Evidence (verified):** `clients/hesta` has 21 labelled sample emails across 8 intents. Some folders have 3, and the `FLS` folder has 1.
  That is too few to gate on. The same emails must not be used both to synthesise and to test.
- **Options:**
  - **A.** The client supplies a held-out labelled sample, never used in synthesis, with a minimum count per regulated intent.
  - **B.** Split the existing 21 into train and test.
  - **C.** Synthetic emails.
- **Recommendation: A.** B is too small to be meaningful, and C tests what we imagine, not what members write.
  Proposed gate (yours to change): no regression against the last approved run, 100% escalation on regulated intents, and
  zero advice leaks.
- **Decision: A adopted,** with the proposed gate: no regression against the last approved run, 100% escalation on regulated
  intents, zero advice leaks.
  **Open:** the minimum held-out size per intent, and the fund supplying the set. No synthesis work starts without it.

### Q3. How is a sender authenticated beyond an email-address match, before memory is read or a case is created?

- **Why it matters:** "verified" currently means the sender address equals the address on the record, and addresses can be spoofed.
- **Evidence (verified):** that is exactly the rule in `classify_verification`. Memory and case creation both depend on it.
- **Options:**
  - **A.** Accept it for the pilot and document the risk.
  - **B.** Also require the inbound message to have passed sender-authentication checks (SPF, DKIM, DMARC) before anything is
    treated as verified. *Unverified:* whether the raw messages in the inbox carry those verdicts. Confirm first.
  - **C.** For regulated actions, a challenge the member answers through a channel the fund already trusts, with a person sending it.
- **Recommendation: B as the minimum for memory and for creating cases, and C for regulated actions.** A is fine only for a
  demo.
- **Decision: B adopted as the minimum for memory and case creation; C for regulated actions.** **An authenticated sender
  means a DMARC pass** (SPF or DKIM passes and aligns with the From: domain). Agreed 2026-10-23. A sender that cannot pass goes to
  a person, and that includes small senders with no email authentication.
  **Finding (verified in the repo):** today the "sender" is the `From:` line of a file, picked out by the Trigger Lambda with a
  pattern match, and nothing reads SPF, DKIM, DMARC or SES verdict headers anywhere. The SES inbound setup is not in this
  repository (the CDK only grants permission to send), so whether raw messages carry those results is unconfirmed here.
  **Interim rule, now implemented (Phase 3): in the strict profile a case is capped at "partial" unless the mail path reports a
  DMARC pass.** The agent now accepts that signal (`sender_authenticated`), but **the Trigger Lambda forwards only the body and the
  `From:` line**, so nothing sets it yet and every strict case is capped. Forwarding it needs a change to the shared Trigger Lambda
  and the SES confirmation below. That change is deliberately not made yet: I cannot confirm the header format from here.
  **Open (blocking):** someone with access to the SES receiving configuration confirms that raw messages in `claims-inbox/`
  carry an `Authentication-Results` header with a DMARC verdict.

### Q4. What does "certain" mean in deterministic terms, per intent?

- **Why it matters:** today it is the model's self-reported confidence, 70 or more. That is poorly calibrated and isn't a safe gate alone.
- **Evidence (verified):** six of the eight intents are regulated and always escalate in code. So "proceed" only matters for the two
  that aren't (`COD` and `RTC`), and only for a verified member.
- **Options:**
  - **A.** For each non-regulated intent, a list of preconditions that must all hold: identity verified, required fields present,
    attachments valid, and model confidence as one input among them. Start with `COD` and `RTC` only.
  - **B.** Keep model confidence as the gate.
  - **C.** Escalate everything in the pilot and have no "proceed" path.
- **Recommendation: A.** C is the safest first release if the preconditions can't be agreed.
- **Decision: A adopted,** starting with `COD` and `RTC` only. **Preconditions agreed 2026-10-22** (a case takes the light
  "draft for a quick staff check" path only if ALL hold; otherwise it gets full human triage). Nothing is ever auto-sent either way.
  - **Baseline, both intents (already enforced by `route_decision`):** intent not regulated; identity verified; confidence at
    least 70; exactly one confident intent; no vulnerability or complaint flags; priority normal or low; no personal advice requested;
    not flagged for triage.
  - **`COD`, also:** the request is **not** to change the email address on record and **not** to restore login access (the
    account-takeover route; the email on record is also the verification factor). Mobile and address updates and how-to
    questions may take the light path.
  - **`RTC`, also:** the message is a **question or a status enquiry** only. Any message that instructs or authorises a rollover or
    transfer (it moves money) gets full triage.
  - **In code since 2026-10-23 (Phase 3, pre-sale).** `route_decision` escalates on both. The intent identifier sets two flags
    (`email_change_or_login_restore`, `instructs_money_movement`) and a pattern check on the message backs them up. **Limits:**
    the flags are set by a model, so they can be missed; the pattern check is deliberately broad for email and login requests but
    only catches instructions that carry a marker word ("please", "I authorise", "go ahead", "I'd like you to"), so a bare
    imperative such as "Transfer my super" relies on the flag. A test records that limit.
  - **Needs HESTA compliance confirmation** that these two intents are correctly non-regulated and the lines are drawn right.

### Q5. Who is asked in the loop, how many times, and what happens at the cap?

- **Why it matters:** "ask in a loop" can mean three different things, and one of them breaks the rule that nothing is sent to members.
- **Evidence (verified):** there is no loop in `config.yaml`. The coordinator is one linear pass, and the reviewer runs once.
- **Options:**
  - **A.** No member-facing loop. A clarifying question is a *draft* a person sends. Any internal retrieval loop is capped, and
    at the cap the case escalates, never guesses.
  - **B.** The agent emails the member directly. This breaks the no-auto-send rule.
  - **C.** No loop.
- **Recommendation: A**, with a cap of 2 iterations and a cost ceiling (the numbers are yours).
- **Decision: A adopted.** No member-facing loop. A clarifying question is a draft a person sends. Internal loops are capped at
  2 iterations plus a cost ceiling, and at the cap the case escalates.
  **Cost ceiling agreed 2026-10-23: US$0.50 per email, in both profiles.** It stops an internal loop and escalates to a person.
  **It is a guess, not a measurement:** no per-email cost is recorded anywhere in the repo. Replace it with a measured figure
  after the first real runs. **The meter is not built, deliberately:** the pipeline has no loop, so there is nothing for a ceiling to stop, and turning token
  usage into dollars needs a price table I cannot verify offline. It belongs with the loop (Phase 3b).

### Q6. What is the human-review SLA, and what happens to a case nobody picks up?

- **Why it matters:** an unattended queue is a compliance failure, especially for complaints. And reviewers who approve nearly everything stop reading.
- **Evidence:** escalations are written to DynamoDB and announced by SNS. I found no age alarm or SLA timer in what I read (verified for
  the pipeline; I did not audit every Lambda). The reviewer's claim that complaint timeframes under RG 271 apply is **unverified**:
  ask compliance.
- **Options:**
  - **A.** An SLA per case class, an alarm on the age of the oldest unattended case, a named owner for the queue, and a recorded
    human decision with a reason on every case (which also becomes labelled data).
  - **B.** Manual monitoring.
- **Recommendation: A.** The Reviewer agent is a model checking a model, so don't count it as a second line of defence.
- **Decision: A adopted.** An SLA per case class, an alarm on the age of the oldest unattended case, a named queue owner, and a
  recorded human decision with a reason on every case.
  **Open:** the SLA by class, and the queue owner.

### Q7. One AWS account per client or shared, and a fork per client or a versioned core plus client data?

- **Why it matters:** forking the code per client means every security fix or model change is patched N times. Sharing an account
  raises the chance of one client's data leaking to another.
- **Evidence:** the repo's own plan says an internal team clones the template and deploys one isolated stack per client (verified).
  Whether that stack isolates keys, memory and roles per client is unknown, because the CDK was not reviewed.
- **Options:**
  - **A.** A versioned core plus a client package (config, pack, graph) as data, and one isolated stack (or account) per client with
    its own keys, memory resource and roles.
  - **B.** A fork per client.
  - **C.** One shared, multi-tenant deployment.
- **Recommendation: A.** The account-versus-stack boundary is a security call for you to make.
- **Decision: A adopted for the code** (a versioned core plus a client package as data). **The boundary is one stack per client,
  in Cognizant's AWS account, because this is for pre-sale activities** (decided 2026-10-23). That departs from the account-per-client
  recommendation, so it comes with conditions:
  1. **Pre-sale only.** The account holds synthetic or de-identified data and no real member data. Moving to a pilot with real
     member data re-opens this question (the recommendation, one account per client, stands for that) and Q9, before anything is
     loaded.
  2. **Isolation inside the shared account.** Each client stack gets its own KMS key, its own IAM roles (no shared wildcard
     access), its own Memory resource and log groups, and a client tag on every resource.
  3. **Infrastructure must be parameterised before a second client is deployed.** The CDK hard-codes `ClaimsAgent-` in resource
     names and a bucket name built from account and region, so a second client stack in the same account would collide. This moves
     the parameterised-infrastructure part of Phase 9 ahead of the second client.
  4. **Permission to use the material.** `clients/hesta` holds HESTA's sample emails, described as de-identified. Confirm HESTA
     permits its materials to be used in a pre-sale environment hosted in Cognizant's account.
  **Open:** who may read each client's stack within the account (least privilege), and the confirmation in item 4.

### Q8. How does a change to a source document reach a re-approved, redeployed artefact?

- **Why it matters:** super law and the fund's documents change. An artefact with no link to its source goes stale unnoticed.
- **Evidence (verified):** packs have an effective-from and effective-to date, but the loader checks them against *today* only, not
  the date of the case, so a past decision can't be replayed under the rules then in force.
- **Options:**
  - **A.** Every rule carries a pointer to its source passage. A change to a source (detected by hash, plus a scheduled review)
    produces a diff against the approved artefact, then re-approval, then a new version with an effective date. The old version is kept.
  - **B.** Manual edits only.
- **Recommendation: A**, and fix the date check to use the case date.
- **Decision: A adopted.** Every rule carries a source pointer; a source change produces a diff, then re-approval, then a new
  effective-dated version, and old versions are kept. The date check will use the case date, not today.
  **Open:** the scheduled review cadence.

### Q9. Where does member data go, and how long does memory keep it?

- **Why it matters:** this is hard to change after the fact, and it concerns personal information at a regulated Australian fund.
- **Evidence (verified):** the default models are `global.` inference profiles, which can route across regions. No retention or
  deletion policy for memory exists.
  **Verified against AWS documentation on 2026-10-23:**
  - Claude Fable 5 (launched 9 June 2026) and Claude Fable 5.1 (launched 1 September 2026) are available on Amazon Bedrock, with a
    1M-token context window. Fable 5.1 has "EOL no sooner than" 1 September 2027.
  - **Anthropic designates Fable 5.1 a "Covered Model", which comes with additional data retention, safety review and access
    policies.** AWS and Anthropic offer Enterprise Frontier Safeguards for eligible customers to use Covered Models while keeping
    data in an environment they control. That bears directly on a regulated fund's data, so read those terms before any real data
    goes near it. The announcement text covers Fable 5.1; I did not verify whether other models carry the designation.
  - Fable's sampling parameters are restricted (temperature must be 1.0 or unset, top_k unsupported) and adaptive reasoning is always
    on. So the "temperature 0 for reproducibility" advice elsewhere in this record does not apply to it.
  - Sydney (`ap-southeast-2`) appears in the regional tables for Claude Haiku 5.5 and Sonnet 4.6, but the extraction lost which of the
    In-Region, Geo and Global columns apply, so **which inference options Sydney actually has is still unconfirmed.**
  *Still unverified:* the Fable 5.1 regional table for Sydney and what the Covered Model data terms require of a super fund; which
  privacy and outsourcing obligations apply (the review cited APRA CPS 230 and the Privacy Act's APP 11): have compliance confirm.
- **Options:**
  - **A.** Use an in-country or regional inference profile if one is available for the chosen models, pin the model version,
    and set a retention period with deletion on request.
  - **B.** Accept `global.` with legal sign-off.
- **Recommendation: A**, and settle it before anything is deployed.
- **Decision: A adopted.** An in-country or regional inference profile if one exists for the chosen models, a pinned model
  version, and a retention period with deletion on request. Settled before any deployment.
  **Model pinned for the pre-sale profile (2026-10-23): Claude Sonnet 4.6 for the main agents
  (`global.anthropic.claude-sonnet-4-6`) and Claude Haiku 4.5 for the fast ones
  (`global.anthropic.claude-haiku-4-5-20251001-v1:0`),** the models the pilot already runs. Fable 5.1 was considered and
  declined for now: it is a Covered Model with additional data retention, and its behaviour on the structured-output agents is
  untested. **Enforced by `tests/test_model_pin.py`:** the config defaults must equal the pin, so a model change needs a deliberate
  edit to the test and a pass through the evaluation harness. An environment variable can still override the default at deploy
  time; the test cannot see that, so deployments must not set `AGENT_MODEL_ID` or `FAST_MODEL_ID` without a record.
  The `global.` prefix permits cross-region routing; acceptable for synthetic data, and **the strict profile must revisit it.**
  **Open:** region policy, retention period, deletion process, and confirming profile and model availability.

---

## Decision

The recommendation is adopted for all nine questions (see each question). The **Open** items are parameters, not choices, and
each has a stated fallback.

### Deployment profiles (decided 2026-10-23)

Two profiles. **Strict is the default.** The pre-sale profile applies only when it is switched on explicitly, may hold synthetic or
de-identified data only, and must stamp every staff report "PRE-SALE DEMO, synthetic data only".

| Decision | Strict (required for any real member data) | Pre-sale (explicit switch) |
|---|---|---|
| Q1 approvals | Signed approval by the named signer for each binding artefact | A named person in the practice approves; signing not required. The loader still refuses an unapproved pack |
| Q2 evaluation | A held-out set from the fund; a regression gate blocks the deploy | The 21 labelled samples run as a smoke test, reported but not a gate |
| Q3 sender authentication | DMARC pass required before "verified". Until the SES setup is confirmed, verification is capped at "partial" | The sender-address match counts as "verified", so memory and the proceed path can be demonstrated |
| Q4 "certain" | The agreed preconditions | The same: they define the behaviour being demonstrated |
| Q5 loop | No member-facing loop; internal loops capped at 2 iterations and a cost ceiling | The same |
| Q6 review queue | SLA, queue owner and an age alarm | Not required (no real queue) |
| Q7 boundary | One account per client (recommended) | One stack per client in Cognizant's account, synthetic data only |
| Q8 source changes | Source pointers, diff, re-approval, scheduled review | Source pointers kept; no scheduled review |
| Q9 data location | Regional profile if available, retention period, deletion on request | Pin the model version; retention and deletion not required for synthetic data |

**Built (Phase 3, 2026-10-23).** `DEPLOYMENT_PROFILE` is read by `core/profile.py`. It is strict unless it is exactly `presale`
(case and spaces ignored); a typo means strict. In strict, a sender who has not passed DMARC is capped at partial; in presale the
address match counts as verified. **Known limit:** the report stamp is an instruction in the coordinator's prompt, so a model
relays it. It is not enforced by code, and a test only checks the instruction is present. **Process control, not technical:**
nothing can detect that data is real, so loading real member data into a pre-sale stack is prohibited by this record, not
prevented by code.

### What each profile is waiting on

| Phase | Strict profile waits on | Pre-sale profile |
|---|---|---|
| 2 (evaluation harness) | Q2 held-out set and size from the fund | Unblocked: 21-sample smoke test |
| 3 (deterministic gates) | Q3 SES confirmation of DMARC verdicts | Unblocked |
| 4 (pack on the other agents) | Phase 3 | Unblocked once Phase 3 is done |
| 5 (deployable core) | Q9 region, retention, model availability | Unblocked for synthetic data; pin the model; parameterise infra first (Q7 item 3) |
| 6 (signed approvals) | Q1 named signers | Not required |
| 7 (human-review operations) | Q6 SLA and queue owner | Not required |

Phase 1 is done. Phase 8 (the drafting tool) needs the approval path and an evaluation set, in either profile.

## Alternatives rejected

None yet. They are recorded per question above.

## Revised roadmap

The earlier roadmap put the synthesis step late but gave it no gates. The review showed it needs three things first: an evaluation
set, deterministic gates and an approval path. So those now come before it, and residency is checked up front.

| Phase | Work | Depends on |
|---|---|---|
| **0** | **Supply the open parameters** listed under Decision. Check residency and pin the model version. No deploy before Q9 | The fund, compliance, security |
| **1** | **Separate core from client inside the YAML port.** The generic parts and the loader become a core package that ships in the container. HESTA's code and config become a client package | Q7 |
| **2** | **Evaluation harness.** A labelled held-out set and a regression runner that blocks a deploy. This is also a corpus check for intent accuracy and escalation recall | Q2 |
| **3** | **Deterministic gates.** Define "certain" per intent in code. Enforce preconditions and ordering at the tool boundary, so a skipped step is refused by something other than a prompt. Add sender authentication | Q3, Q4 |
| **4** | **Attach the pack to the other agents** with per-agent rules and their local tools listed. Real-AWS acceptance test for memory isolation | Phase 3 |
| **5** | **Deployable core.** A Dockerfile, locked dependencies, `agentcore.json` pointed at the YAML port, validated and synthesised only. Pack delivery by build-time copy into the client's image | Q7 |
| **6** | **Signed approvals and an approver registry** covering every binding artefact, with the date check using the case date | Q1, Q8 |
| **7** | **Human-review operations.** SLA, queue-age alarm, a recorded decision and reason per case, and an audit record per decision (rule version, model and prompt version, what was recalled, the human's action) | Q6 |
| **8** | **Offline drafting tool.** It reads a client's documents and an LLM *proposes* a feature file, vocabulary and thresholds as a diff, with a source pointer per rule, pinned model, hashed inputs and scanned input. A person approves it. It is never run at deploy time | Phases 2, 6; Q1, Q8 |
| **9** | **Client scaffold command and parameterised infrastructure** | Phases 1 to 5 |

**What moved, and why:**
- The drafting tool went from "step 6" to Phase 8, because it must not exist before the evaluation set and the approval path do.
- The evaluation harness is new, and it is a deploy gate.
- Residency and model pinning are now Phase 0.
- Deterministic gates and human-review operations are new phases. The review found "certain" undefined and the review queue unowned.
- Memory in the deployed app is out of scope, since it is legacy.

## Progress

**Phase 1 is done (2026-10-22).** The YAML port is split into a client-neutral `core/` (the rule-pack compiler and loader, the
Gherkin parser, the agent factory, the Gateway client, memory, generic settings) and a client package `hesta/` bound to it by a
thin factory. `config.yaml` is unchanged in what it references.
- **Verified:** 159 tests pass, and the real strands-compose loader still builds all 10 agents. The drift runner, the three
  packs' checks and the demo are unchanged. A test copies only the app folder to a temp directory and loads a pack there.
- **Enforced:** a test fails if `core/` names a client, imports a client package, or needs anything outside the app folder.
**Phase 3 is done for the pre-sale profile (2026-10-23).** Built: the profile switch, sender authentication feeding verification,
the agreed `COD` and `RTC` rules in the routing gate, and the stamp. The routing feature file now has 57 scenarios (7 new).
- **Verified:** 186 tests pass, including mutation checks that fail when the profile or the pattern backstop is broken; the
  real loader builds all 10 agents; the spec runs against the YAML port (47 pass, the one known drift unchanged).
- **Not built, with reasons:** the cost meter (no loop yet); forwarding authentication results from the Trigger Lambda (needs the
  SES confirmation); the strict-profile gates that need the fund's input (signers, held-out set, SLA).

- **Not done in Phase 1:** `rules/run_feature.py` still holds HESTA's routing-gate steps and adapters. Splitting that into a
  generic runner and a client part belongs with Phase 2.

## Consequences

- **Slower to the first synthesis demo,** but nothing synthesised can reach production without evidence and approval.
- **Several answers need people outside engineering:** compliance (Q1, Q6, Q9), security (Q3, Q7) and HESTA (Q2, Q4).
- **The YAML port is committed to as the core.** The old apps stay as labelled legacy references.
- **Unverified claims in this record** (SES verdict headers, complaint timeframes, APRA and Privacy Act obligations, Australian
  inference-profile options for the chosen models, the Covered Model data terms) must be confirmed before they are relied on. Model
  availability on Bedrock is now verified.
