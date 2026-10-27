# ADR-0015: Spin-up and runtime design: nine open questions, and the revised roadmap

## Status

**Proposed. Waiting for answers.** Each question has a recommendation and an empty **Your answer** line. Nothing in the
roadmap below is final until the questions it depends on are answered.

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
- **Your answer:** A. 
  *Also needed: who signs, by role, for each regulated intent.* ______________________________________________

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
- **Your answer:** minimum held-out size per intent ______ ; metrics and thresholds ______________________________________

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
- **Your answer:** Go for B.

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
- **Your answer:** preconditions for `COD` ____________________ ; for `RTC` ____________________

### Q5. Who is asked in the loop, how many times, and what happens at the cap?

- **Why it matters:** "ask in a loop" can mean three different things, and one of them breaks the rule that nothing is sent to members.
- **Evidence (verified):** there is no loop in `config.yaml`. The coordinator is one linear pass, and the reviewer runs once.
- **Options:**
  - **A.** No member-facing loop. A clarifying question is a *draft* a person sends. Any internal retrieval loop is capped, and
    at the cap the case escalates, never guesses.
  - **B.** The agent emails the member directly. This breaks the no-auto-send rule.
  - **C.** No loop.
- **Recommendation: A**, with a cap of 2 iterations and a cost ceiling (the numbers are yours).
- **Your answer:** A

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
- **Your answer:** SLA by class ______________________ ; queue owner ______________________

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
- **Your answer:** ______________________________________________

### Q8. How does a change to a source document reach a re-approved, redeployed artefact?

- **Why it matters:** super law and the fund's documents change. An artefact with no link to its source goes stale unnoticed.
- **Evidence (verified):** packs have an effective-from and effective-to date, but the loader checks them against *today* only, not
  the date of the case, so a past decision can't be replayed under the rules then in force.
- **Options:**
  - **A.** Every rule carries a pointer to its source passage. A change to a source (detected by hash, plus a scheduled review)
    produces a diff against the approved artefact, then re-approval, then a new version with an effective date. The old version is kept.
  - **B.** Manual edits only.
- **Recommendation: A**, and fix the date check to use the case date.
- **Your answer:** review cadence ______________________________________________

### Q9. Where does member data go, and how long does memory keep it?

- **Why it matters:** this is hard to change after the fact, and it concerns personal information at a regulated Australian fund.
- **Evidence (verified):** the default models are `global.` inference profiles, which can route across regions. No retention or
  deletion policy for memory exists. *Unverified:* which Australian or regional profiles exist for the models you want. Whether the
  model you have in mind ("Claude Fable") is available on Bedrock in your region. Which privacy and outsourcing obligations apply
  (the review cited APRA CPS 230 and the Privacy Act's APP 11): have compliance confirm.
- **Options:**
  - **A.** Use an in-country or regional inference profile if one is available for the chosen models, pin the model version,
    and set a retention period with deletion on request.
  - **B.** Accept `global.` with legal sign-off.
- **Recommendation: A**, and settle it before anything is deployed.
- **Your answer:** region policy ____________ ; memory retention ____________ ; deletion process ____________

---

## Decision

*To be filled in once the answers above are complete.* Until then, nothing in the roadmap below that depends on a question
should be built.

## Alternatives rejected

None yet. They are recorded per question above.

## Revised roadmap

The earlier roadmap put the synthesis step late but gave it no gates. The review showed it needs three things first: an evaluation
set, deterministic gates and an approval path. So those now come before it, and residency is checked up front.

| Phase | Work | Depends on |
|---|---|---|
| **0** | **Answer Q1 to Q9.** Check residency and pin the model version. No deploy before Q9 | You |
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

## Consequences

- **Slower to the first synthesis demo,** but nothing synthesised can reach production without evidence and approval.
- **Several answers need people outside engineering:** compliance (Q1, Q6, Q9), security (Q3, Q7) and HESTA (Q2, Q4).
- **The YAML port is committed to as the core.** The old apps stay as labelled legacy references.
- **Unverified claims in this record** (sender-authentication headers, complaint timeframes, APRA and Privacy Act obligations,
  Australian inference-profile availability, model availability) must be confirmed before they are relied on.
