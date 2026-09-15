# HESTA v2 Enhancement TODO

## Purpose

Use this document as the implementation source of instruction for improving
the HESTA v2 member-email workflow.

Scope is limited to the v2 implementation:

* Deployed AgentCore Runtime: `hestaclaimsagent_v2`
* Runtime source code: `app/hesta-claimsagent`
* Gateway Lambdas: `lambdas/member_lookup`, `lambdas/case_lookup_creation`,
  `lambdas/email_review`
* Trigger Lambda: `lambdas/trigger`
* v2 infrastructure: `agentcore/agentcore.json` and `agentcore/cdk`
* v2 tests and configuration

Do not modify the old-version code unless a shared interface must be changed
for v2 compatibility. Do not add outbound email sending, production
dashboards, or unrelated features.

## Implementation rules

1. Preserve the current v2 safety boundary: the agent drafts email but never
   sends it automatically.
2. Fail closed. If identity, review, validation, Gateway, or configuration
   checks fail, route to human review rather than inventing data or approving
   a response.
3. Do not use an unlimited LLM retry loop. All revision loops must have a
   configurable maximum.
4. Keep AWS resource names and environment variables v2-specific.
5. Do not expose account-specific information unless the identity and
   authorization checks allow it.
6. Do not treat a model instruction as an authorization decision.
7. Reuse existing Pydantic models, taxonomy helpers, Gateway helpers, logging,
   and test conventions before adding new abstractions.
8. Surface real errors. Do not silently convert Gateway, DynamoDB, or
   configuration failures into successful-looking results.
9. Add or update focused tests for every behavior change.
10. Keep all changes surgical and verify the relevant tests before completion.

## Execution order

Implement the work in this order:

1. Save conversation history to the Cases table
2. Reuse matching cases and pass history to the Writer
3. Attachment presence handling
4. Writer/reviewer revision loop
5. Account-detail disclosure controls (low priority)
6. Deterministic final-output validation (low priority)
7. Human-review audit metadata

The case conversation contract should be stable before wiring it into case
reuse, Writer revisions, and human-review records.

---

## TODO 1: Save conversation history to the Cases table

### Priority

High

### Objective

Persist the member conversation and generated draft on the case so future
messages can be processed with their prior context.

### Required implementation

1. Add a `conversation_history` field to each Cases DynamoDB item.
2. Store structured entries for:
   * inbound member email;
   * generated draft;
   * Reviewer feedback and revision outcomes, when available.
3. Include timestamps, entry type, and pipeline/runtime version for each entry.
4. Append the current inbound email and final draft after processing.
5. Store only the data required for workflow continuity. Do not store secrets
   or unnecessary sensitive data.
6. Use a bounded history or a separate conversation table if the case item
   could exceed DynamoDB's 400 KB item limit.
7. Make history writes safe for retries and concurrent updates.
8. Update the case Lambda response/model contract to expose history when the
   case is reused.

### Acceptance criteria

* A newly created case contains the current inbound email in its history.
* The final draft is stored against the same case.
* Reviewer feedback and revision outcomes are retained when available.
* Reprocessing the same event does not duplicate the same history entry.
* The case remains below DynamoDB item-size limits.

---

## TODO 2: Reuse the member's case and pass history to the Writer

### Priority

High

### Objective

Ensure a member has exactly one ongoing case that accumulates conversation
history across every enquiry, instead of creating duplicate cases per
message or per topic — and ensure that accumulated history is provided to
the Writer alongside the current email's own intent.

### Design decision: one case per member

A case is the member's relationship record, not a record of one specific
enquiry. `primary_intent_id` and case `status` are **not** part of the case
lookup/reuse decision:

* A member's repeat contact — regardless of topic, and regardless of
  whether their existing case is open or closed — reuses that same case.
  Do not filter by status and do not compare intents when deciding whether
  to reuse a case.
* The *current* message still gets its own `primary_intent_id` from the
  Intent Identifier (AI-001) as normal. That per-message intent is not used
  to decide case reuse; it is used for the Writer's snippet selection, the
  attachment assessment, and routing, exactly as today.
* Each stored history entry should carry the intent that message was
  associated with (see TODO 1), so the Writer can see how the member's
  intent has shifted across the life of the case, not just the current
  intent.
* A reply to a case that is `closed` is still the same case: reuse it, and
  let the Writer/Reviewer see the closed status in the passed context so it
  can give a status-appropriate reply (e.g. referencing the prior
  resolution) rather than silently re-actioning a resolved matter.

### Current flow and required rule

The Runtime performs `member_lookup` before `case_lookup_creation`. A case
lookup must proceed only after a member has been found and identity has been
established. If identity cannot be established, route the request to human
review and do not create or select a case through the normal verified-member
path.

After identity is established, `case_lookup_creation` queries cases by
`member_id` alone. If any case exists for that member, reuse it — regardless
of its stored intent or status. Only create a new case when the member truly
has none yet. If more than one case exists for a member (e.g. from legacy
data before this rule), selection must be deterministic (e.g. the earliest
by `created_at`, then `case_id`) rather than arbitrary.

### Required implementation

1. Require a successful verified `member_id` before normal case lookup or
   creation.
2. Query existing cases by the `member_id-index` GSI.
3. Store `primary_intent_id` on every case at creation, for audit/reporting
   only — it does not gate reuse.
4. Reuse the member's existing case whenever one exists, irrespective of its
   stored intent or status (open, pending, closed, etc.).
5. Create a new case only when the member has no case at all yet.
6. When an existing case is reused, retrieve its `conversation_history` and
   pass it to the Context Manager and Writer together with the current
   email and the current message's own intent. Clearly delimit historical
   content from the current request, and preserve the intent recorded
   against each historical entry where available.
7. Add an idempotency key derived from the strongest available source:
     * conversation/thread ID;
     * S3 bucket + object key + ETag;
     * deterministic message hash.
8. Use DynamoDB conditional writes or an equivalent atomic operation to
    prevent duplicate case creation during retries or concurrent invocations.
9. Return the selected or created case ID and conversation history in a
    stable response shape.
10. Update the Runtime, Pydantic models, and human-review payload if the
    response shape changes.

### Acceptance criteria

* A known member's follow-up email — on any topic, regardless of intent
  match — reuses their existing case.
* A request without an established member identity is routed to human review
  and does not enter the verified-member case path.
* A matching existing case provides previous email/draft history, tagged
  with the intent recorded at the time, to the Context Manager and Writer.
* A different intent on a follow-up still reuses the member's existing case
  rather than creating a new one.
* Duplicate trigger delivery does not create duplicate cases.
* A reply to a closed case reuses that same case rather than creating a new
  one; the Writer is informed the case is closed.
* Concurrent case-creation requests are safe.
* Existing-case selection is deterministic (one case per member) and covered
  by tests.

---

## TODO 3: Pass attachment presence to the Writer

### Objective

Ensure the attachment validator tells the Writer whether an attachment is
present so the Writer can request a missing attachment or avoid requesting one
that is already present.

### Current behavior

Email normalization already counts attachment markers such as
`[ATTACHMENT form.pdf]`. The validator already receives that count and returns
an attachment assessment. The v2 pilot should keep this simple:

* `attachments_present == 0`: no attachment is present;
* `attachments_present > 0`: an attachment is present and may be treated as
  valid for the pilot.

No binary file inspection, document-type verification, completeness check, or
maximum attachment count is required for this TODO.

### Required implementation

1. Keep the existing attachment marker detection.
2. Keep intent-specific expected-document descriptions where useful for the
   Writer's missing-document request.
3. Change the present status from `present_unverified` to `present`, or clearly
   define `present_unverified` as present-and-accepted for this pilot.
4. Include the attachment assessment in the Writer prompt.
5. Instruct the Writer:
   * request the expected attachment when no attachment is present and the
     intent requires one;
   * do not request it again when one or more attachment markers are present.
6. Include the attachment assessment in Reviewer input and human-review
   metadata.

### Acceptance criteria

* The Writer receives an explicit present/missing attachment signal.
* The draft requests a required document only when no attachment is present.
* The draft does not request an attachment again when a marker is present.
* The pilot treats any detected attachment marker as present and valid.
* The implementation does not claim to inspect attachment bytes.

---

## TODO 4: Add a bounded Writer–Reviewer revision loop

### Objective

Allow the Writer to revise a draft using Reviewer feedback until the Reviewer
approves it or a safe maximum is reached.

### Current behavior

The Writer creates one draft. The Reviewer evaluates it once. Reviewer output
is displayed, but its issues and suggested edits are not sent back to the
Writer.

### Required implementation

1. Add a configuration value:

   ```text
   MAX_DRAFT_REVISIONS=2
   ```

2. Validate that the value is a non-negative bounded integer.
3. Keep the first Writer draft as revision `0`.
4. If the Reviewer approves the draft, stop immediately.
5. If the Reviewer rejects the draft and revisions remain:
   * send the original member request;
   * send the current draft;
   * send Reviewer issues;
   * send Reviewer suggested edits;
   * send identity state and attachment requirements;
   * request a revised structured `DraftEmail`.
6. Re-run the Reviewer after every revision.
7. Keep machine-authoritative fields authoritative:
   * primary intent;
   * verification state;
   * safety flags;
   * attachment state.
8. If the Reviewer fails, stop the loop and route to human review.
9. If the maximum is reached, stop the loop and route to human review.
10. Include revision count and the final Reviewer result in the output and
    human-review record.
11. Never let a revision remove required identity verification or compliance
    wording merely to satisfy a style suggestion.

### Acceptance criteria

* An approved first draft performs no unnecessary extra Writer call.
* A rejected draft is revised with the actual Reviewer feedback.
* The loop stops at `MAX_DRAFT_REVISIONS`.
* Reviewer failure cannot produce an approved draft.
* Personal-advice refusal remains intact through all revisions.
* The final draft is always available for human review when not approved.

---

## TODO 5: Prevent unauthorized account-detail disclosure — LOW PRIORITY

### Priority

Low

### Objective

Ensure the v2 agent does not disclose account-specific information to an
unverified or unauthorized sender.

### Design requirement

Authorization must be enforced by application logic and data access policy.
The Bedrock Guardrail is a safety backstop, not the identity decision.

### Required implementation

1. Define explicit disclosure states:
   * `unverified`: no account-specific details;
   * `partially_verified`: only explicitly approved procedural information;
   * `verified`: only data returned by authorized tools;
   * `lookup_failed`: no account-specific details.
2. Do not pass unauthorized account data to the Writer.
3. Update the Writer prompt with explicit disclosure rules.
4. Update the Reviewer prompt to reject:
   * balances;
   * transaction details;
   * contribution history;
   * payment amounts;
   * tax information;
   * bank details;
   * member/account identifiers;
   * unsupported dates, eligibility, or outcomes.
5. Extend the v2 Bedrock Guardrail with a topic for sensitive account or
   personal-data disclosure where appropriate.
6. Keep the existing personal-financial-advice Guardrail behavior.
7. Add deterministic post-generation checks for obvious sensitive values and
   prohibited account-detail patterns.
8. Ensure logs and human-review records do not unnecessarily duplicate
   sensitive account data.

### Acceptance criteria

* An unverified member receives only general information and verification
  instructions.
* A verified response contains only authorized tool-returned information.
* Personal advice remains blocked.
* A draft containing unauthorized account details is rejected or redacted and
  routed to human review.
* Guardrail configuration and application-level disclosure checks are both
  tested.

---

## TODO 6: Add deterministic final-output validation — LOW PRIORITY

### Priority

Low

### Objective

Add a non-LLM validation step after Writer revisions and before a draft is
marked approved for human sending.

### Why this is needed alongside the Guardrail

The Bedrock Guardrail remains responsible for model-level content safety,
including personal financial advice and any configured sensitive-data topics.
This validator is not a replacement for the Guardrail. It enforces
v2-specific application rules that require workflow context, including whether
the member is verified, whether the Writer was given authorized data, and
whether required identity or attachment wording is present.

### Required checks

Validate the final subject and body for:

* unauthorized account details, based on the identity state and authorized
  tool output;
* personal financial or investment advice that was not blocked by the
  Guardrail;
* unsupported monetary values;
* unsupported dates, eligibility, approval, or timing promises;
* missing identity-verification wording;
* missing required attachment requests;
* internal prompts, system instructions, or tool details;
* Guardrail sentinel text.

### Required behavior

* A validation failure must set the draft to not approved.
* The failure reason must be visible to the Reviewer and human reviewer.
* Do not silently delete content without recording what happened.
* If the validator cannot determine safety, route to human review.

---

## TODO 7: Improve human-review audit records

### Objective

Make the DynamoDB human-review record sufficient for staff action,
troubleshooting, and evaluation.

### Required fields

Extend the `email_review` payload and table item with, where available:

* `case_id`;
* `review_id`;
* final draft subject/body;
* `status`;
* escalation reasons;
* primary intent;
* sender type;
* identity/verification state;
* attachment assessment;
* Reviewer approval and check results;
* Reviewer issues and suggested edits;
* revision count;
* validation failures;
* source object identifier;
* processing timestamp;
* pipeline/version identifier.

Do not store secrets or unnecessary raw sensitive data.

### Acceptance criteria

* A staff member can understand why the item was escalated without reading
  application logs.
* The record identifies the related case and final draft.
* Revision and Reviewer history is traceable.
* Failed writes surface errors and do not appear successful.

---

## Definition of done

This enhancement set is complete only when:

* all TODOs above are implemented or explicitly marked out of scope;
* v2-only unit and integration tests pass;
* the bounded revision loop cannot run indefinitely;
* unauthorized account details are blocked by application logic;
* attachment requirements are configurable per intent;
* repeated conversations do not create duplicate cases;
* human-review records contain sufficient audit context;
* existing v2 deployment configuration remains valid;
* no outbound email sending has been introduced;
* `git diff --check` passes for all changed files.
