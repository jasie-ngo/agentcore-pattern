# HESTA v2 Member-Email Workflow

## Purpose

This document explains what the HESTA v2 agent does when a member email is
received, how each step is performed, and which AWS service or Lambda is
involved. It describes the implementation in `app/hesta-claimsagent`,
`lambdas`, and `agentcore/cdk`; the code is the source of truth.

The pilot is an **asynchronous email-to-case workflow**. It prepares a draft
for HESTA staff and records work requiring human attention. It does **not** send
emails or make regulated member decisions automatically.

## End-to-end view

```text
Email file uploaded to S3
        |
        v
Amazon EventBridge (Object Created rule)
        |
        v
Lambda: AgentCore-ClaimsAgentV2-dev-Trigger
  - reads the object
  - builds an invocation payload
  - signs an HTTPS request with SigV4
        |
        v
Amazon Bedrock AgentCore Runtime
  hestaclaimsagent_v2 / HestaMemberEmailAgent
        |
        +--> Amazon Bedrock model agents
        |      Intent Identifier
        |      Context Manager
        |      Empathy Agent
        |      Writer
        |      Reviewer & Editor
        |
        +--> AgentCore Memory
        |
        +--> AgentCore Gateway (MCP over HTTPS)
                |
                +--> Lambda: AgentCore-ClaimsAgentV2-dev-MemberLookup
                |       -> DynamoDB Members table
                |
                +--> Lambda: AgentCore-ClaimsAgentV2-dev-CaseLookupCreation
                |       -> DynamoDB Cases table
                |
                +--> Lambda: AgentCore-ClaimsAgentV2-dev-EmailReview
                        -> DynamoDB HumanReview table
```

The exact physical Lambda names are derived from the CDK stack name. In the
current CDK code they are:

* `AgentCore-ClaimsAgentV2-dev-Trigger`
* `AgentCore-ClaimsAgentV2-dev-MemberLookup`
* `AgentCore-ClaimsAgentV2-dev-CaseLookupCreation`
* `AgentCore-ClaimsAgentV2-dev-EmailReview`

## AWS services and their responsibilities

| AWS service | Responsibility in this workflow |
|---|---|
| Amazon S3 | Receives the inbound email file under the configured inbox prefix. |
| Amazon EventBridge | Detects an S3 `Object Created` event and invokes the trigger Lambda. |
| AWS Lambda | Runs the S3 trigger and the three Gateway tool targets. |
| Amazon Bedrock AgentCore Runtime | Hosts and runs the HESTA v2 container entry point. |
| Amazon Bedrock models | Run the intent, context, empathy, writing, and review agents. The configured primary model is Claude Sonnet; fast classification agents use Claude Haiku. |
| Amazon Bedrock Guardrails | Denies personal financial/investment/product advice on Writer model output. |
| Amazon Bedrock AgentCore Gateway | Exposes the three Lambda tools through MCP and protects them with a Cognito JWT authorizer. |
| Amazon Cognito | Supplies the OAuth M2M client used by the Runtime to authenticate to Gateway. |
| AgentCore Identity | Retrieves the Gateway credential from the configured credential provider/token vault. |
| Amazon DynamoDB | Stores members, cases, and human-review records. |
| AgentCore Memory | Provides semantic and summarization recall for repeat contacts from the same member. |
| Amazon SQS | Dead-letter queue for failed trigger-Lambda invocations. |
| Amazon CloudWatch | Alarm for messages visible in the trigger dead-letter queue. |
| OpenTelemetry / AgentCore observability | Emits invocation and model/tool traces for operational review and evaluation. |

## 1. Ingress: email arrives in S3

**AWS services:** Amazon S3, Amazon EventBridge.

An email-like text file is placed in the inbox bucket. The CDK bucket enables
EventBridge notifications. The EventBridge rule listens for:

* source `aws.s3`
* detail type `Object Created`
* an object in the `claims-inbox/` prefix

The bucket is an ingestion boundary, not an email-sending service. The current
pilot accepts HESTA contact-form text, direct/threaded email text, JSON, or
plain text.

## 2. Trigger Lambda reads and submits the email

**Lambda:** `lambdas/trigger/handler.py`  
**Deployed function:** `AgentCore-ClaimsAgentV2-dev-Trigger`  
**AWS services:** AWS Lambda, Amazon S3, AgentCore Runtime.

The trigger Lambda:

1. Reads the bucket and object key from the EventBridge event.
2. Calls `s3.get_object`.
3. Decodes the object as UTF-8.
4. Detects the input shape:
   * HESTA contact form (`form based mail` and `Values:`)
   * traditional email headers (`From:` or `Subject:`)
   * JSON
   * unstructured text
5. Builds a payload containing:
   * `prompt`
   * `source`
   * optional `claimant_email`
6. Sends a signed HTTPS request to the AgentCore Runtime invocation endpoint.
7. Reads only a small streaming preview and returns once the Runtime accepts
   the request.

This is deliberately **fire-and-forget**. The Lambda does not wait for the
agent pipeline to finish and does not receive the final draft. The agent writes
durable results to DynamoDB through Gateway tools.

The Lambda uses SigV4 with its execution-role credentials and the
`bedrock-agentcore` service name. It has a 90-second timeout, two retry
attempts, and an SQS dead-letter queue.

## 3. Runtime parses the invocation

**AWS service:** Amazon Bedrock AgentCore Runtime.  
**Code:** `app/hesta-claimsagent/main.py`.

The Runtime invokes the `invoke` entry point. The entry point:

1. Accepts the trigger payload (or an AgentCore local-development wrapper).
2. Starts one `invoke_agent` OpenTelemetry span for evaluation.
3. Runs `_run_pipeline`.
4. Streams a human-readable Markdown result.

The payload parser accepts a JSON object, a JSON string, or plain text. The
pipeline obtains:

* the raw email prompt
* the sender email (`claimant_email` or `sender_email`)
* the source identifier

## 4. Phase 0: deterministic email normalization

**AWS service:** None; this runs inside the Runtime container.  
**Code:** `ingestion/email_normalizer.py`.

Before any model call, `normalize_email()` converts the raw input into an
`InboundEmail` envelope. It:

* identifies `contact_form`, `direct_email`, or `third_party`
* removes warning banners and the HESTA legal footer
* removes quoted thread history from the latest message
* parses contact-form fields
* counts `[ATTACHMENT ...]` markers
* extracts a sender email when possible
* extracts a real-looking member number for lookup
* treats placeholders such as `[MEMBER NUMBER]` as missing
* identifies solicitor/professional correspondence markers

This step is deterministic and does not use an LLM. The trigger Lambda is not
responsible for this normalization; it only forwards the object content.

## 5. Memory session is selected

**AWS service:** AgentCore Memory.  
**Code:** `memory/session.py` and `main.py`.

The Runtime chooses a stable actor key from:

1. the normalized member number, or
2. the sender email, or
3. `anonymous` when neither is available.

The identifier is sanitized for AgentCore Memory. A new session ID is created
for each invocation, while the actor ID allows cross-session recall.

The configured Memory resource uses:

* a semantic strategy for member facts
* a summarization strategy for session history
* a 90-day event expiry

If Memory cannot be initialized, processing continues without recall and the
Runtime logs the condition. After the Context Manager produces its summary, the
current interaction is explicitly recorded because structured-output calls do
not automatically trigger the session manager's write hook.

## 6. Gateway session and authentication

**AWS services:** AgentCore Gateway, Amazon Cognito, AgentCore Identity.  
**Code:** `tools/gateway.py`.

The Runtime creates a fresh MCP client for each invocation on the request/event
loop thread. This is important because the
`@requires_access_token` decorator needs the AgentCore workload-identity
context to obtain a Cognito M2M token.

The flow is:

1. AgentCore Identity retrieves the `cognito-gateway-m2m` credential.
2. The Runtime obtains an OAuth token with the `agentcore/invoke` scope.
3. The MCP client connects to the Gateway over streamable HTTP.
4. Gateway authenticates the request with its Cognito custom-JWT authorizer.
5. The Runtime starts the client before making tool calls.

Gateway tool names are resolved at runtime because Gateway namespaces tools as
`<target>___<tool>`. The three logical tool names used by the agent are:

| Logical tool | Gateway target | Lambda |
|---|---|---|
| `member_lookup` | `member-lookup` | `AgentCore-ClaimsAgentV2-dev-MemberLookup` |
| `case_lookup_creation` | `case-lookup-creation` | `AgentCore-ClaimsAgentV2-dev-CaseLookupCreation` |
| `email_review` | `email-review` | `AgentCore-ClaimsAgentV2-dev-EmailReview` |

## 7. Phase 1: Understand

The Runtime renders this phase as `## 1 · Understand`.

### 7.1 AI-001 Intent Identifier

**AWS services:** Amazon Bedrock model; AgentCore Runtime.  
**Code:** `agents/intent_identifier.py`.

The fast model classifies the latest message against HESTA's fixed intent
taxonomy. It returns:

* all detected intents and confidence scores
* the primary intent
* sender type
* whether the request needs human triage
* whether personal financial advice was requested

The result is constrained to the taxonomy. An invalid primary ID or model
failure becomes `other_unknown` with human triage enabled.

Personal-advice detection is separate from intent detection. For example, a
rollover question can still be a rollover intent while also being flagged as a
personal-advice request.

### 7.2 AI-002 Context Manager

**AWS services:** Amazon Bedrock model, AgentCore Memory, AgentCore Gateway,
AWS Lambda, DynamoDB.  
**Code:** `agents/context_manager.py`.

The Context Manager creates a concise operational summary containing:

* what the member wants
* the conversation state
* outstanding items

It then performs two deterministic Gateway tool calls.

#### Member identity lookup

The Context Manager calls `member_lookup`:

* by `member_id` when normalization found one
* otherwise by sender email
* otherwise it records that no lookup identifier is available

The Gateway invokes **`AgentCore-ClaimsAgentV2-dev-MemberLookup`**
(`lambdas/member_lookup/handler.py`). The Lambda:

* uses `GetItem` on the `Hesta-members` DynamoDB table for `member_id`
* uses the `email-index` GSI for email lookup
* returns the member record or an error

The current table key is `member_id`, with `email` as a global secondary index.

#### Case lookup or creation

The Context Manager calls `case_lookup_creation` with the resolved member ID
and/or sender email. The Gateway invokes
**`AgentCore-ClaimsAgentV2-dev-CaseLookupCreation`**
(`lambdas/case_lookup_creation/handler.py`). The Lambda:

1. Queries the `member_id-index` GSI when a member ID is present.
2. Returns existing cases when any are found.
3. Otherwise creates a `CASE-XXXXXXXX` record with status `Open`.
4. Marks the new case as `verified` when a member ID exists, otherwise
   `unverified`.

The result is either `existing_cases_found`, `new_case_created`, or an error.

## 8. Phase 2: Decide

The Runtime renders this phase as `## 2 · Decide`.

### 8.1 Identity-derived profile

**AWS services:** AgentCore Gateway, AWS Lambda, DynamoDB (from the previous
step).

The identity result is converted into a `MemberProfile`:

* an active matched member is treated as verified
* a missing/failed lookup is unverified
* an inactive member requires verification

The profile is used by attachment assessment, writing, and the routing gate.

### 8.2 AI-004 Attachment Validation

**AWS service:** None; deterministic code in the Runtime.

For a verified member, the pipeline compares the number of attachment markers
with the expected document for the primary intent. It returns:

* `not_applicable`
* `missing`
* `present`

The pilot sees attachment markers only; it does not receive or inspect file
bytes. Any detected marker is treated as present and accepted for the pilot,
so the Writer asks for a missing document only when none was detected, and
never re-asks once a marker is present. A human must still confirm the type,
completeness, and legibility of a document.

For an unverified member, attachment assessment is skipped.

### 8.3 AI-005 Empathy Agent

**AWS service:** Amazon Bedrock model; AgentCore Runtime.

For a verified member, the fast model assesses:

* sentiment
* vulnerability flags
* complaint indicator
* operational priority
* recommended human attention

For an unverified member, the pipeline uses a deterministic neutral assessment
that says identity verification is required. This avoids over-interpreting an
email before identity is established.

### 8.4 Deterministic human-routing gate

**AWS service:** None; deterministic code in `routing.py`.

The request escalates when any of the following is true:

* the primary intent is regulated
* personal advice was requested
* identity is not verified
* intent confidence is below the configured threshold (70 by default)
* intent is `other_unknown` or flagged for triage
* multiple confident intents were found
* vulnerability flags exist
* priority is high or urgent

The gate does not approve or reject a member request. It decides whether a
human must be involved before anything is sent.

## 9. Phase 3: Execute

The Runtime renders this phase as `## 3 · Execute`.

### 9.1 AI-011 Writer creates a draft

**AWS services:** Amazon Bedrock model and Bedrock Guardrails; AgentCore
Runtime.

The Writer uses the stronger configured model and HESTA inline knowledge
snippets. Its prompt includes the intent, verification state, case summary,
empathy result, attachment assessment, and the member's message.

The Writer:

* creates a subject and body
* asks for identity details when verification is needed
* avoids promises about eligibility, approval, amounts, or timing
* uses only supplied HESTA snippets and the member message
* does not send the email

The Writer model can have the Bedrock `NoPersonalAdvice` guardrail attached.
There is also an application-level deterministic advice-decline draft. If
advice is detected or the guardrail blocks output, the draft declines to give
personal advice and refers the member to HESTA advice services.

If the Writer model fails, a safe HESTA-voice template fallback is generated.

### 9.2 AI-012 Reviewer & Editor checks the draft

**AWS service:** Amazon Bedrock model; AgentCore Runtime.

For verified members, the Reviewer checks:

* accuracy
* tone
* compliance
* whether identity verification is requested when required
* whether the draft contains personal advice

It sets `approved_for_human_send` only when all checks pass. A reviewer failure
is fail-safe: the draft is not approved and a human must review it.

The draft and review are streamed as Runtime output for staff visibility. No
email service is called and no automatic send occurs.

### 9.3 Human-in-the-loop record

**AWS services:** AgentCore Gateway, AWS Lambda, DynamoDB.

When the routing gate escalates, the Runtime calls `email_review` if
`ENABLE_HITL_RECORD` is enabled.

The Gateway invokes **`AgentCore-ClaimsAgentV2-dev-EmailReview`**
(`lambdas/email_review/handler.py`). The Lambda writes an item to the
`Hesta-humanreview` DynamoDB table containing:

* a generated `review_id`
* the related `case_id`
* the draft subject and body
* escalation reasons
* `created_at`
* status `pending_review`

This DynamoDB write is the pilot's hand-off boundary. There is no SNS
notification or outbound email in the current HESTA v2 path.

If case lookup did not produce a case ID, the Runtime surfaces a warning and
does not invent one. If the Gateway call fails, the actual error is surfaced
in the streamed result.

## 10. Phase 4: Learn and observe

**AWS services:** AgentCore Memory, OpenTelemetry/AgentCore observability,
AgentCore evaluation.

The Runtime renders `## 4 · Learn` and notes that:

* invocation, model, and tool activity is traced with OpenTelemetry
* the current interaction is written to AgentCore Memory when available
* human edits can be captured as feedback in a future post-pilot extension

The Runtime uses one top-level `invoke_agent` span and child structured-output
spans so session evaluation can find the invocation. Online evaluation is
configured for the v2 runtime with Helpfulness, Correctness, and Tool Selection
Accuracy metrics.

## 11. Main data records

### Members table

Created by CDK as `Hesta-members` with a stack-derived prefix.

* Partition key: `member_id`
* GSI: `email-index`
* Read by: `AgentCore-ClaimsAgentV2-dev-MemberLookup`

### Cases table

Created by CDK as `Hesta-cases` with a stack-derived prefix.

* Partition key: `case_id`
* GSI: `member_id-index`
* Read and written by: `AgentCore-ClaimsAgentV2-dev-CaseLookupCreation`

### Human-review table

Created by CDK as `Hesta-humanreview` with a stack-derived prefix.

* Partition key: `review_id`
* Written by: `AgentCore-ClaimsAgentV2-dev-EmailReview`

## 12. Important behavior branches

### Verified member

1. Member lookup returns a record.
2. Case lookup finds or creates a case.
3. Attachment validation and empathy assessment run.
4. The routing gate evaluates all identity, intent, attachment, and empathy
   signals.
5. A draft is created and reviewed.
6. If escalation is required, a pending human-review record is written.

### Missing or unverified identity

1. Lookup uses the available email, or reports that no identifier exists.
2. Case lookup can create an unverified case when no existing case is found.
3. Attachment and empathy analysis are skipped or replaced with safe defaults.
4. The routing gate escalates because identity verification is required.
5. The Writer drafts a request for identity details.
6. The draft is recorded for human review when a case ID is available.

### Personal advice request

1. Intent Identifier sets `personal_advice_requested`.
2. Routing escalates.
3. Writer uses the deterministic advice-decline draft (and the Bedrock
   Guardrail remains a platform backstop).
4. Reviewer must not approve advice-containing output.
5. The draft and escalation reason are recorded for human handling.

### Gateway unavailable

The pipeline can still normalize, classify, summarize, and draft with fallback
behavior, but identity and case data cannot be retrieved. The Runtime surfaces
Gateway errors rather than fabricating member or case data. A human-review
record cannot be written unless a case ID is already available.

## 13. V2 stack deployment notes

The v2 stack is defined by `agentcore/agentcore.json` and the CDK resources in
`agentcore/cdk/lib/infra-construct.ts`. Its AgentCore runtime is
`hestaclaimsagent_v2`, and its Gateway exposes only these HESTA v2 tools:

* `member_lookup`
* `case_lookup_creation`
* `email_review`

The CDK derives the DynamoDB tables, Lambda functions, queue, alarm,
EventBridge rule, and Bedrock Guardrail names from the v2 stack name. The S3
inbox bucket is currently configured with the literal name
`hesta-poc-agentcore-s3`, so the v2 deployment requires that bucket name to be
available in the target AWS account and region.

The implemented v2 hand-off ends when the relevant DynamoDB record is written:

* a case record in the cases table, or
* a pending human-review record in the human-review table.

Outbound email delivery, staff dashboards, binary document parsing, and
production-grade identity verification are not part of this v2 workflow.

## Code entry points

* Runtime orchestration: [`app/hesta-claimsagent/main.py`](../app/hesta-claimsagent/main.py)
* Email normalization: [`app/hesta-claimsagent/ingestion/email_normalizer.py`](../app/hesta-claimsagent/ingestion/email_normalizer.py)
* Routing gate: [`app/hesta-claimsagent/routing.py`](../app/hesta-claimsagent/routing.py)
* Gateway client: [`app/hesta-claimsagent/tools/gateway.py`](../app/hesta-claimsagent/tools/gateway.py)
* Trigger Lambda: [`lambdas/trigger/handler.py`](../lambdas/trigger/handler.py)
* Member Lambda: [`lambdas/member_lookup/handler.py`](../lambdas/member_lookup/handler.py)
* Case Lambda: [`lambdas/case_lookup_creation/handler.py`](../lambdas/case_lookup_creation/handler.py)
* Review Lambda: [`lambdas/email_review/handler.py`](../lambdas/email_review/handler.py)
* Infrastructure: [`agentcore/cdk/lib/infra-construct.ts`](../agentcore/cdk/lib/infra-construct.ts)
