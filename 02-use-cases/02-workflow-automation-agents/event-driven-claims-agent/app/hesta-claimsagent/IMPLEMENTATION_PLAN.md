# HESTA Member-Email Agentic Platform: Implementation Plan

> **Status:** DRAFT for review; **no code has been changed yet.** This document is the
> agreed blueprint we will implement against, inside
> `02-use-cases/02-workflow-automation-agents/event-driven-claims-agent/app/hesta-claimsagent`.
>
> **This is a PILOT, and it implements the full agent pipeline** (all agents in §2/§5:
> AI-001 Intent, AI-002 Context, AI-003 Identity, AI-004 Attachment Validation, AI-005 Empathy,
> AI-011 Writer, AI-012 Reviewer/Editor). To move fast, three things **reuse existing primitives**
> rather than new build: **(1)** identity/verification uses the existing **DynamoDB `lookup_policy`**
> check; **(2)** human-in-the-loop stays exactly as today: **persist a case/review record to
> DynamoDB through the MCP Gateway** (`request_human_review` / `create_claim`); **(3)** the **Writer
> displays the draft reply email as its output** to the current user, so nothing is auto-sent.
> **§0 records these reuse decisions; §5 is the pipeline we build.**
>
> **Sources analysed:** `hesta/Hesta-POC.pptx` (proposed capabilities & future-state
> journey), the 23 de-identified email samples in `hesta/{BDBN,BP,COD,DASP,FH,FLS,NOI,RTC}/`,
> the master `hesta/Cognizant Emails.xlsx` (intent taxonomy + volumes), and the current
> agent code (`main.py`, `routing.py`, `tools/structured_output.py`, `memory/session.py`,
> `config.py`) plus the S3→EventBridge→Runtime trigger (`lambdas/trigger/handler.py`).

---

## 0. Pilot scope & reuse decisions (READ FIRST)

**The pilot implements the full agent pipeline**: every processing agent in §2/§5
(AI-001 Intent, AI-002 Context, AI-003 Identity, AI-004 Attachment Validation, AI-005 Empathy,
AI-011 Writer, AI-012 Reviewer/Editor), running end-to-end over each inbound "email" (any file
dropped in the S3 inbox). **§5 is the pipeline; §8 is the build order.** What makes it a *pilot* is
three deliberate **reuse decisions** that avoid new infrastructure:

### The three reuse decisions (fixed for the pilot)

1. **Identity & verification → reuse the existing DynamoDB check.** AI-003 does **not** get a new data
   store and **the table structure is not changed**. It reuses the current `lookup_policy` Gateway tool +
   `PoliciesTable` **exactly as they are** (see §6.1): look the record up by its existing key
   (policy/member number), then **verify by comparing the sender email and account/policy type against
   that record** to derive a verification level. **No new GSI in the pilot**: reverse-lookup by email
   (which would need an index) is post-pilot only.

2. **Human-in-the-loop → exactly as implemented today: write a record to DynamoDB via the MCP Gateway.**
   When a case needs a human (regulated intent, unverified client, or low confidence), the pipeline
   **persists a record through the existing MCP Gateway tools**: `request_human_review` (writes the
   **Reviews** table) and/or `create_claim` (writes the **Claims** table). **That DynamoDB record *is*
   the hand-off.** No new human console, queue, or workflow is built in the pilot; a human reads the
   record out-of-band. This reuses today's Phase-2 routing (`resolve_routing`/`decide_action`) and the
   deterministic Phase-3 tool calls **verbatim**.

3. **Writer output → the draft email is displayed to the current user, never sent.** AI-011 renders a
   full HESTA-voice reply and **`yield`s it as the agent's streamed output**. **No SES / `send_notification`**
   in the pilot, so a human copies/edits/sends it.

Everything else (Memory, Identity, Gateway, cost-based model routing, graceful degradation) is reused
unchanged. **AI-013 (Human Feedback Learning)** and the **AI Dashboard** are cross-cutting foundations,
not per-email agents; in the pilot they are satisfied by **reusing existing primitives only**
(capture human edits/corrections to **Memory**; observability via the already-enabled OTEL/traces), not
new build.

### AWS resources: UNCHANGED in the pilot

**No AWS infrastructure is created or altered.** The pilot changes only **application code** inside
`app/hesta-claimsagent` (the agent container), which is rebuilt and redeployed to the existing Runtime.

| Resource | Pilot impact |
|---|---|
| **AgentCore Memory** | **Unchanged**: reused as-is (same memory resource, same namespaces) |
| **DynamoDB** (Policies / Claims / Reviews) | **No structure change**: same tables, keys, GSIs. We only **read** Policies and **write items** to Claims/Reviews via existing tools. Seeding sample records with `seed_dynamodb.py` is **data, not schema.** |
| **S3 → EventBridge → Trigger Lambda** | **Unchanged**: same bucket, same rule, same Trigger Lambda. Email normalisation happens **inside the agent**, not in the trigger. |
| **Gateway / Identity / Cognito / Cedar / SES** | **Unchanged**: reuse existing tools & auth; SES simply isn't called (no send) |
| **Runtime container** | Code changes are deployed here (expected, since this *is* the agent we're building) |

> If we later want reverse-lookup by sender email, a proper `MembersTable`, or dedicated
> `create_case`/`kb_search` tools, those **are** infra changes, explicitly **post-pilot** (§5/§8).

### Pipeline at a glance (full detail in §5)

```
S3 object → Trigger Lambda (unchanged) → Runtime:
  normalise (InboundEmail)  ← done INSIDE the agent, not the trigger
  UNDERSTAND  AI-001 Intent (+attachment detection)   →  AI-002 Context summary
  DECIDE      AI-003 Identity  (REUSE lookup_policy/DynamoDB)
              AI-004 Attachment validation  ·  AI-005 Empathy
              → routing gate (REUSE resolve_routing/decide_action)
  EXECUTE     AI-011 Writer  → DRAFT EMAIL shown as output (no send)
              AI-012 Reviewer/Editor  → checks the draft
              → human-in-the-loop = WRITE RECORD to DynamoDB via MCP
                 (request_human_review / create_claim)  when escalated/regulated
  LEARN       AI-013 corrections → Memory   ·   Dashboard → existing observability
```

### Writer (AI-011) specifics

- Drafts in HESTA's house style; the outbound samples give the exact voice:
  *"Thank you for contacting HESTA / Hi [MEMBER NAME] … We're here to help, The team at HESTA"* + legal
  footer. "Approved knowledge" for the pilot = a **small inline per-intent snippet set** lifted from the
  real outbound replies (**no Bedrock Knowledge Base needed yet**).
- Adapts to verification state: **unverified →** draft the identity-verification request (member number,
  full name, DOB, address, as the samples do); **verified →** draft an intent-appropriate
  acknowledgement / next-steps reply. **AI-012** then validates tone/accuracy/compliance of that draft.
- **Output = the email text, displayed** via `yield` (same mechanism as today's phase output).

---

## 1. Context & Goal

Today `app/hesta-claimsagent` is a **generic "SecureGuard Insurance" dual-agent claims demo**:

- **Phase 1: Claims Processor** (Sonnet): extracts claim, calls `lookup_policy`, decides `ACCEPT`/`REJECT`.
- **Phase 2: Validation Agent** (Haiku): scores `CONFIDENCE` 0–100, routes `AUTO_APPROVE`/`HUMAN_REVIEW`.
- **Phase 3: Deterministic Execution** (no LLM): `create_claim` / `request_human_review` / `send_notification`.

We are **repurposing this scaffold** into HESTA's **member-email agentic operations platform**. The
business does **not** want auto-approval of regulated decisions; it wants AI to **understand, prepare,
and draft** while **humans remain responsible for regulated assessments and approvals**.

The POC ingestion model stays event-driven: **any file dropped into the S3 inbox is treated as an
inbound "email"** (we are not yet connected to a live mailbox). S3 object created → EventBridge →
Trigger Lambda → AgentCore Runtime.

### What we keep (the scaffold's good bones)

- AgentCore **Runtime** (containerised Strands agents) + **Gateway** (MCP tools, Cognito M2M JWT,
  Cedar policy) + **Memory** (semantic recall / summarisation) + **Identity** (token vault).
- The **structured-output tool** pattern (agents emit typed tool calls instead of prose we regex).
- The **deterministic execution phase** (no extra LLM once decisions are made).
- **Cost-based model routing** (cheap model for classification, stronger model for reasoning/writing).
- **Graceful degradation** (Memory / Gateway unavailable → keep working).

### What changes (business reframe)

| Insurance demo (now) | HESTA target |
|---|---|
| `ACCEPT`/`REJECT` a claim | **Identify member intent(s)** and prepare the case |
| `lookup_policy` | **Identity & profile match** (member number / name / DOB) |
| Auto-approve on high confidence | **Human approves all regulated actions**; AI drafts & recommends |
| Single claim payload | **Threaded emails + contact-form submissions + attachments** |

---

## 2. Proposed Capabilities (from Hesta-POC.pptx)

The deck defines a logical business flow, **UNDERSTAND → DECIDE → EXECUTE → LEARN**, supported by
these capabilities (IDs are HESTA's):

| ID | Capability | Flow stage | Pilot? | Role in our pipeline |
|---|---|---|---|---|
| **AI-001** | **Intent Identifier & Attachment Detection** | UNDERSTAND | ✅ **pilot** | Why is the member contacting us (one or more reasons)? Business scenario + **confidence**; detect attachments |
| **AI-002** | Conversation Context Manager | UNDERSTAND | ✅ **pilot** | Reconstruct the thread; produce a concise operational case summary |
| **AI-003** | Identity & Profiling Agent | DECIDE | ✅ **pilot** (reuse DynamoDB) | Verify a valid client via the **existing `lookup_policy` DynamoDB check** (member/policy no., type, sender email); see §0 & §6.1 |
| **AI-004** | Attachment Validation Agent | DECIDE | ✅ **pilot** | Identify document type, assess completeness, highlight missing/invalid info |
| **AI-005** | Empathy Agent | DECIDE | ✅ **pilot** | Detect vulnerability, complaint indicators, sentiment, operational priority; recommend attention level |
| **AI-011** | Writer | EXECUTE | ✅ **pilot** (draft shown, not sent) | Draft a HESTA-voice reply email, **displayed as agent output** for staff to review/send |
| **AI-012** | Reviewer & Editor | EXECUTE | ✅ **pilot** | Validate accuracy, tone, completeness, compliance of the draft before a human sends |
| **AI-013** | Human Feedback Learning | LEARN (cross-cutting) | ◐ pilot (reuse Memory only) | Capture human corrections to **Memory**; no new build |
| **AI Dashboard** | Platform capability | LEARN (cross-cutting) | ◐ pilot (reuse observability) | Reuse already-enabled OTEL traces/metrics; no new build |
| **(reuse)** | **Human-in-the-loop = DynamoDB record via MCP** | DECIDE/EXECUTE | ✅ **pilot** (reuse) | Existing Phase-2 routing + `request_human_review`/`create_claim` **write a record to DynamoDB**: that record is the human hand-off |

**Early release focus (deck slides 2–3):** the **Financial Hardship (FH)** journey: coordinated
AI assistance with **human oversight for the regulated decision**.

**KPIs to instrument** (deck): Executive → member experience, first-contact resolution.
Operational → intent & routing accuracy, average handling time, resolution time, cost to serve,
repeat/clarification rate, communication clarity.

---

## 3. Intent Taxonomy (derived from the email samples)

The sample **folder names are our ground-truth labels**. Mapping to HESTA's `Type` field
(`Cognizant Emails.xlsx` → "Email List") gives us the canonical intent set. Signals below are drawn
directly from the sample content.

| Code | `intent_id` | HESTA Type | Key signals (from real samples) | Common sub-scenarios | Regulated? |
|---|---|---|---|---|---|
| **BDBN** | `death_benefit_nomination` | Death Benefit / Binding Death Benefit Nomination | "binding death nomination", "beneficiary", "nominate who my super goes to", attached *Binding Death Nomination form* | Submit form; view current nomination; change/remove beneficiary; binding vs non-binding education | Yes |
| **BP** | `withdrawal_benefit_payment` | Withdrawal / Benefit Payment | "withdraw my super", form reason *"Accessing super"*, over-65 / retirement, "status of my withdrawal application" | How to withdraw; check application status; provide ID verification | Yes |
| **COD** | `change_of_details` | Change of Personal Details | form reason *"Updating my account details"*, "change my mobile", "update address", "can't log in / restore access" | Update phone/email/address; restore MOL access; accessibility needs | No (but ID-verify) |
| **DASP** | `departing_australia_payment` | Departing Australia Superannuation Payment (DASP) | "left/leaving Australia", "temporary resident", overseas address, NZ/KiwiSaver | Eligibility; how to claim; overseas bank/address | Yes |
| **FH** | `financial_hardship` | Financial Hardship | "financial hardship", "$10,000", "can't pay bills", "early release", bank-statement evidence | Eligibility; application status; document upload help; EOFY timing | **Yes (early-release focus)** |
| **FLS** | `family_law_split` | Family Law Split | **solicitor/law-firm sender**, "family law", "superannuation split", "procedural fairness", "court date" | Info request; procedural fairness; document exchange | Yes |
| **NOI** | `notice_of_intent_tax_deduction` | Notice of Intent to Claim a Tax Deduction | "notice of intent", "NOI", "claim a tax deduction", "personal/concessional contribution", ATO NOI form | How to lodge; confirm receipt; submission issue | Yes |
| **RTC** | `rollover_transfer_combine` | Rollover / Transfer / Combine Accounts | "roll over", "transfer/combine my super", "consolidate", other-fund names (Rest, KiwiSaver), "funds not showing after transfer" | How to roll over; combine accounts; transfer status chase | No (but ID-verify) |
| N/A | `other_unknown` | (fallback) | none of the above / ambiguous / general enquiry | General enquiry; misc | n/a → human triage |

### Taxonomy rules the Intent Identifier must follow

1. **Multi-intent** is real, e.g. a member updating their address **and** asking about departing
   Australia (DASP+COD). Return a **list** of intents, each with its own confidence.
2. **DASP vs BP** overlap: DASP is a *specific* withdrawal for departing temporary residents. Prefer
   DASP when residency/departure signals are present; otherwise BP.
3. **Sender type** matters: `member`, `non_member` (prospective, seen in RTC), `solicitor/third_party`
   (seen in FLS). Capture it, since it changes routing and verification.
4. **Confidence** is mandatory (0–100) per intent; **low confidence or `other_unknown` → human triage**,
   never a regulated action.
5. **Conversation state** (not an intent): identity-verification sub-flows appear across categories.
   Flag `awaiting_identity_verification` rather than inventing an intent for it.

---

## 4. Email shapes & normalisation (Phase 0 foundation)

The samples show **two inbound shapes** plus heavy noise. A normalisation step must run **before**
any agent sees the text.

**Shape A: Contact-Us web form** (structured):
```
You've received a new form based mail from https://www.hesta.com.au/.../contact-us.html
Values: enquiry-sent-from : A member | email-address : [MEMBER EMAIL] | member-number : [MEMBER NUMBER]
        name : [MEMBER NAME] | phone : [PHONE NUMBER] | reason-for-enquiry : Accessing super
        message : <the actual ask>
```
`reason-for-enquiry` is a **coarse hint only** (values seen: *Accessing super, Updating my account
details, My online account, Other*), so never trust it as the final intent.

**Shape B: Direct/threaded email**: leading `WARNING: This email originated from outside…` banner,
`[ATTACHMENT FILENAME]` markers, quoted `From:/Sent:/To:/Subject:` history, and a long
`Issued by H.E.S.T. Australia Ltd …` legal footer.

**The normalisation output is a canonical `InboundEmail` envelope:**

```jsonc
{
  "message_id": "…",
  "channel": "contact_form | direct_email | third_party",
  "sender_type": "member | non_member | solicitor | unknown",
  "from": "[MEMBER EMAIL]",
  "subject": "…",
  "received_at": "…",
  "member_number": "[MEMBER NUMBER] | null",
  "form_reason": "Accessing super | null",   // contact-form hint only
  "latest_message": "the current actionable text (banners/footers/quotes stripped)",
  "thread": [ { "direction": "inbound|outbound", "text": "…" } ],
  "attachments": [ { "filename": "[ATTACHMENT FILENAME]", "present": true } ],
  "raw": "original text"
}
```

Normalisation tasks: strip the security banner and the standard HESTA legal footer; split the
**latest message** from the quoted thread; parse contact-form key/values; extract de-identified
placeholders (`[MEMBER NUMBER]`, `[DATE OF BIRTH]`, `[ADDRESS]`, …); collect `[ATTACHMENT FILENAME]`
markers and a count. This is deterministic Python (regex), **not** an LLM call.

> **De-identification note:** samples use placeholder tokens. The Intent Identifier must classify from
> **semantics**, not PII. Identity matching (AI-003) keys off the member-number placeholder for the POC.

---

## 5. Target architecture & pipeline (the pilot pipeline)

> **This is what the pilot builds.** All agents run; the **§0 reuse decisions** apply: identity reuses
> the DynamoDB `lookup_policy` check, human-in-the-loop is a **DynamoDB record written via the MCP
> Gateway**, and the Writer's draft is **displayed, not sent**. Each capability is an isolated,
> independently testable module.

```
S3 object (any file) → EventBridge → Trigger Lambda (UNCHANGED)
   → AgentCore Runtime pipeline:
     normalise into InboundEmail  (deterministic; INSIDE the agent, not the trigger)

     UNDERSTAND
       AI-001 Intent Identifier + Attachment Detection  → IntentResult (intents[], attachments[], confidence)
       AI-002 Conversation Context Manager              → CaseSummary
     DECIDE
       AI-003 Identity & Profiling  (REUSE lookup_policy + PoliciesTable, §6.1)  → MemberProfile (+ verification_level)
       AI-004 Attachment Validation                     → AttachmentAssessment
       AI-005 Empathy                                   → EmpathyAssessment (vulnerability, priority)
       →  Routing gate (REUSE resolve_routing/decide_action): regulated | unverified | low-confidence → HUMAN_REVIEW
     EXECUTE
       AI-011 Writer            → DraftEmail  →  **YIELD as agent output (displayed, NOT sent)**
       AI-012 Reviewer & Editor → ReviewResult (accuracy/tone/compliance of the draft)
       → human-in-the-loop = **write a record to DynamoDB via MCP Gateway**
            request_human_review (Reviews table)  and/or  create_claim (Claims table)   ← the hand-off
     LEARN  (cross-cutting — reuse only)
       AI-013 Human Feedback Learning  → corrections captured to existing Memory
       AI Dashboard                    → existing OTEL traces/metrics → KPIs
```

**Human-in-the-loop is the default for regulated intents** (BDBN, BP, DASP, FH, FLS, NOI). AI prepares
the case and drafts the reply; the **hand-off to a human is a DynamoDB record written via the MCP
Gateway** (`request_human_review` → Reviews table, and/or `create_claim` → Claims table), reusing
today's mechanism unchanged. **Nothing is auto-sent in the pilot**: the Writer's draft is displayed to
the current user, and a human reviews the record + draft out-of-band before sending. Non-regulated
informational replies (e.g. RTC/COD status updates) are handled the same draft-for-human-review way in
the pilot.

### Model routing (reuse the existing cheap/strong split)

- **Cheap/fast model** (Haiku, `FAST_MODEL_ID`): AI-001 intent classification, AI-005 empathy,
  AI-004 attachment typing; classification-style tasks.
- **Strong model** (Sonnet, `AGENT_MODEL_ID`): AI-002 summarisation, AI-011 Writer, AI-012 Reviewer.

---

## 6. Proposed code structure in `app/hesta-claimsagent`

Additive and modular. Existing files are refactored, not thrown away.

```
app/hesta-claimsagent/
├── main.py                      # REWORK: orchestrator for UNDERSTAND→DECIDE→EXECUTE→LEARN
├── config.py                    # EXTEND: model ids, thresholds, feature flags per capability
├── routing.py                   # REWORK: intent + confidence + empathy + regulated → action/gate
├── models.py                    # NEW: Pydantic schemas for every structured output (§7)
├── ingestion/
│   └── email_normalizer.py      # NEW: InboundEmail envelope (Phase 0, deterministic)
├── intents/
│   └── taxonomy.py              # NEW: the 8 intents + signals + few-shot examples (single source of truth)
├── agents/
│   ├── intent_identifier.py     # NEW: AI-001 (build first)
│   ├── context_manager.py       # NEW: AI-002
│   ├── identity_profiling.py    # NEW: AI-003
│   ├── attachment_validation.py # NEW: AI-004
│   ├── empathy.py               # NEW: AI-005
│   ├── writer.py                # NEW: AI-011
│   └── reviewer_editor.py       # NEW: AI-012
├── prompts/                     # NEW: system prompts per agent (kept out of code for iteration)
├── knowledge/
│   └── hesta_snippets.py        # NEW: inline per-intent reply snippets for the Writer (pilot; no KB)
├── tools/
│   └── structured_output.py     # EXTEND: submit_* tools per capability (typed tool calls)
└── memory/session.py            # KEEP: reuse; key actor on member_number when available
```

**Gateway tools: pilot reuses existing ones; no new Lambdas required:**
- **Identity (AI-003):** reuse **`lookup_policy`** + `PoliciesTable` as-is (§0/§6.1). **No GSI / no
  schema change in the pilot**: verify by comparing sender email + type against the resolved record.
- **Human-in-the-loop:** reuse **`request_human_review`** (writes Reviews table) and **`create_claim`**
  (writes Claims table); the DynamoDB record is the hand-off. No `create_case`/`update_case` needed yet.
- **Writer knowledge (AI-011):** inline `knowledge/hesta_snippets.py` for the pilot, meaning **no `kb_search`
  Lambda / Bedrock KB** yet.
- **Not used in the pilot:** `send_notification` (SES), since nothing is auto-sent.
- Existing **Cedar policies** continue to forbid autonomous regulated actions.

*(Post-pilot, these graduate to dedicated tools: `lookup_member`, `create_case`/`update_case`,
`get_case_context`, `kb_search`; see §5/§8 roadmap.)*

---

## 6.1 Reusing today's DynamoDB verification for AI-003 (Identity & Profiling)

> **Pilot vs. graduation:** in the **pilot** we reuse `lookup_policy` + `PoliciesTable` **exactly as they
> are: no rename, no GSI, no schema change** (§0). The table further below (`MembersTable`, `email-index`
> GSI, `lookup_member`) is the **post-pilot graduation** and is included only to show where this grows.

**Yes, the verification already in the code is the right foundation for AI-003 and we should reuse it.**

### What exists today

- **`lookup_policy`** Gateway tool (`lambdas/policy_lookup/handler.py`) → `get_item` on **PoliciesTable**
  (partition key `policy_number`, no GSI). Returns the full record: `holder_name`, `email`,
  `policy_type`, `coverage_amount`, `deductible`, `status` (`active`/`expired`) + nested vehicle/property.
- The **Claims Processor** (`PROCESSOR_PROMPT` in `main.py`) is instructed to **call lookup first and
  never fabricate details**; a failed/absent lookup → **REJECT / escalate** (safe fallback). The result
  is captured via structured output and acted on in the deterministic Phase 3.
- Records are seeded by `scripts/seed_dynamodb.py`.

**What this actually is:** a DynamoDB-backed **record-existence + status check keyed on a single ID**.
The record already carries `email` and `policy_type`, **but nothing today cross-checks the sender email**
against the record (that field is present yet unused for verification), and there is **no way to look a
record up by email** (no GSI).

> Terminology: don't confuse this with the Phase-2 **Validation Agent**, which scores *decision
> confidence*, not identity. The identity/verification mechanism this request refers to is
> **`lookup_policy`**, and it maps directly onto **AI-003** and the deck's "human verification before
> regulated actions" gate.

### Fit for HESTA: reuse verdict

The whole pattern (Gateway tool → Lambda → DynamoDB `get_item`, **agent-must-verify-first**, **safe
fallback on miss**, deterministic execution, Cedar enforcement) maps 1:1 to *"confirm a valid member
before any regulated action."* We keep the mechanism and adapt the data model.

| Today (insurance) | HESTA reuse (AI-003) |
|---|---|
| `PoliciesTable`, PK `policy_number` | `MembersTable`, PK `member_number` (same shape) |
| fields `holder_name / email / policy_type / status` | `full_name / email / account_type / member_status` + `dob`, `address` |
| lookup by `policy_number` **only** | lookup by `member_number` **or** `email` (**add `email-index` GSI**) or `name+dob` |
| `email` present but **unused** | AI-003 **cross-checks sender email** vs record `email` as a verification factor |
| miss → REJECT | miss / insufficient identifiers → `verification_required = true` → identity sub-flow / human |

### Concrete, minimal changes (design only, no code yet)

1. **Rename/duplicate the tool:** `lookup_policy` → **`lookup_member`** (Lambda + `schemas/lookup_member.json`).
   Input widens from `{policy_number}` to **`{member_number?, email?, full_name?, dob?}`**.
2. **Add one GSI** (`email-index`) to the members table so the **sender's From address**, the most
   reliable inbound signal, resolves a member even when the member number is missing or buried in the body.
3. **Return the match + which identifiers matched**, so AI-003 can compute a *verification level* rather
   than a bare found/not-found.
4. **Keep the safe-fallback posture verbatim:** never act on an unmatched/unverified member: escalate to
   a human. (This is already how the code treats a failed lookup.)
5. **Reuse `seed_dynamodb.py`** to seed synthetic members that mirror the sample scenarios.

### The request's three factors → a verification level

Using the requested signals: **policy/member number, policy/account type, and sender email**, plus the
name/DOB the samples show HESTA asking for:

- **`verified`**: enough factors match (e.g. `member_number` + `email`, or `name`+`dob`+`address`):
  AI may assemble the profile and prepare/draft. **A human still approves every regulated action.**
- **`partial` / `unverified`**: triggers the **identity-verification sub-flow** the samples repeatedly
  show (request member number, full name, DOB, address). Modelled as conversation state
  `awaiting_identity_verification` (see AI-002), **not** a new intent.

**Net:** one table rename + one GSI + one generalised tool turns the *passive* `email`/`policy_type`
fields into *active* verification signals, and reuses the code's existing "verify-before-you-act" safety
for free.

---

## 7. Structured-output contracts (`models.py`)

Every agent emits a typed object via a `submit_*` tool (same pattern as today's `submit_decision`).
Draft schemas:

```python
# AI-001
class DetectedIntent(BaseModel):
    intent_id: str            # one of the taxonomy ids (§3) or "other_unknown"
    confidence: int           # 0-100
    rationale: str
    evidence_quote: str       # short span from latest_message supporting it

class AttachmentInfo(BaseModel):
    filename: str
    present: bool
    inferred_type: str        # "binding_death_nomination_form" | "bank_statement" | "noi_form" | "unknown"
    readable: bool | None     # None = unknown at detection time (validated later by AI-004)

class IntentResult(BaseModel):
    intents: list[DetectedIntent]        # ordered, highest confidence first (multi-intent aware)
    primary_intent_id: str
    sender_type: str                     # member | non_member | solicitor | unknown
    attachments: list[AttachmentInfo]
    needs_human_triage: bool             # true if low confidence / other_unknown / conflicting

# AI-002
class CaseSummary(BaseModel):
    summary: str
    conversation_state: str              # e.g. "awaiting_identity_verification", "new_request", "chasing_update"
    outstanding_items: list[str]

# AI-003 — backed by the reused DynamoDB lookup (lookup_member; see §6.1)
class MemberProfile(BaseModel):
    member_number: str | None
    matched: bool                        # a member record was found in DynamoDB
    match_key: str | None                # how we found it: "member_number" | "email" | "name_dob"
    factors_matched: list[str]           # e.g. ["member_number","email","account_type","name_dob"]
    account_type: str | None             # ex-"policy_type" (super | pension | ttr | …)
    member_status: str | None            # active | closed
    verification_level: str              # verified | partial | unverified
    verification_required: bool          # gate before any regulated action

# AI-004
class AttachmentAssessment(BaseModel):
    filename: str
    document_type: str
    complete: bool
    missing_or_invalid: list[str]

# AI-005
class EmpathyAssessment(BaseModel):
    sentiment: str                       # positive | neutral | negative
    vulnerability_flags: list[str]       # e.g. ["financial_distress","accessibility_need","bereavement"]
    complaint_indicator: bool
    priority: str                        # low | normal | high | urgent
    recommended_attention: str

# AI-011 Writer — PILOT: the draft is DISPLAYED as agent output, never auto-sent
class DraftEmail(BaseModel):
    subject: str
    body: str                            # full HESTA-voice reply, shown to staff to review/send
    intent_id: str                       # which intent this reply addresses
    verification_state: str              # verified | needs_verification (drives the draft's ask)
    kb_snippets_used: list[str]          # pilot: inline per-intent snippets (not a Bedrock KB yet)
    assumptions: list[str]

# AI-012 Reviewer & Editor — PILOT: reviews the DraftEmail before it's shown for human send
class ReviewResult(BaseModel):
    approved_for_human_send: bool
    accuracy_ok: bool; tone_ok: bool; compliance_ok: bool
    edits: str; issues: list[str]
```

---

## 8. Phased delivery

> **The pilot builds all of these phases** (all agents), in this order. Apply the **§0 reuse decisions
> throughout**: identity reuses `lookup_policy`/DynamoDB (don't build a new store), human-in-the-loop is
> a DynamoDB record written via the MCP Gateway (`request_human_review`/`create_claim`), and the Writer
> **displays** its draft (no SES). The dedicated tools mentioned below (`lookup_member`, `create_case`,
> `kb_search`) are the **post-pilot** graduation of those reused pieces, not pilot work.

Ordered so the **Intent Identifier ships first** and each later phase is independently valuable.

### Phase 0: Foundations (prereq for everything)
- `ingestion/email_normalizer.py` (InboundEmail), `intents/taxonomy.py`, `models.py`.
- Normalisation runs **inside the agent entrypoint** (`main.py`) on the content the existing Trigger
  Lambda already forwards; **the Trigger Lambda is not modified** (handle contact-form text, threaded
  email, plain text, JSON there).
- **Eval fixtures**: export the 23 samples to fixture files; folder name = ground-truth label.

### Phase 1: AI-001 Intent Identifier + Attachment Detection ← **first deliverable**
- `agents/intent_identifier.py` + `submit_intent` tool + `IntentResult`.
- Prompt built from `intents/taxonomy.py` (signals + few-shot from real samples).
- Attachment detection from `[ATTACHMENT FILENAME]` markers (+ inferred type).
- Routing: high-confidence known intent → downstream stub / case creation;
  low-confidence / `other_unknown` / multi-conflict → **human triage**.
- **Acceptance:** intent-accuracy eval vs the 8 folder labels; multi-intent and attachment cases
  detected; every output has a confidence and a rationale.

### Phase 2: UNDERSTAND completion
- AI-002 Conversation Context Manager (thread reconstruction + operational summary).
- AI-003 Identity & Profiling (identifier match; `verification_required` gate): **pilot reuses
  `lookup_policy` + `PoliciesTable` AS-IS** (§0/§6.1): match on policy/member number, account/policy
  type, and sender email; seed synthetic records with `seed_dynamodb.py`. (Renaming to `lookup_member`
  / a `MembersTable` / an `email` GSI is the post-pilot graduation, not pilot work.)

### Phase 3: DECIDE support
- AI-004 Attachment Validation (document type/completeness, e.g. Binding Death Nomination form present
  & legible? bank statement attached for FH?).
- AI-005 Empathy (vulnerability/complaint/priority: FH financial distress, COD accessibility need,
  BDBN bereavement, FLS legal urgency).
- Deterministic **governance gate** in `routing.py`: regulated intent → `HUMAN_REQUIRED`.

### Phase 4: EXECUTE (Writer + Reviewer; Financial Hardship first)
- AI-011 Writer: drafts from **inline `knowledge/hesta_snippets.py`** (pilot; no `kb_search`) and
  **`yield`s the `DraftEmail` as agent output, displayed, not sent**. AI-012 Reviewer & Editor checks
  accuracy/tone/compliance of that draft.
- Human-in-the-loop = **write a record to DynamoDB via the MCP Gateway** (`request_human_review` /
  `create_claim`) for regulated/unverified/low-confidence cases. **No SES send.**
- Exercise the **FH journey end-to-end** first (deck's early release), then the other intents.

### Phase 5: LEARN (cross-cutting)
- AI-013 Human Feedback Learning: capture human edits/overrides as structured records
  (Memory + a feedback store) to improve prompts/few-shots.
- AI Dashboard: emit metrics/traces (reuse existing observability) mapped to the deck KPIs
  (intent & routing accuracy, AHT, resolution time, repeat/clarification rate, cost to serve, FCR).

---

## 9. Testing & evaluation

- **Unit (offline):** normaliser (banner/footer strip, contact-form parse, thread split, attachment
  count); taxonomy few-shot integrity; routing/governance gate logic (mirror existing `tests/`).
- **Intent eval harness:** the 23 samples are a labelled set (folder = label). Measure precision/recall
  per intent, multi-intent handling, and confidence calibration. Track accuracy as prompts evolve.
- **Scenario tests:** identity-verification sub-flow; solicitor (FLS) sender; non-member (RTC);
  DASP-vs-BP disambiguation; attachments-present-but-unreadable (BDBN).
- **Guardrail test:** regulated intents can **never** reach an autonomous send/approve; always gated
  to a human (Cedar policy + routing test).

---

## 10. Open questions / assumptions for sign-off

1. **Scope:** confirmed; the pilot implements **all agents** (AI-001..AI-005, AI-011, AI-012), with the
   §0 reuse decisions (DynamoDB identity, HITL-as-DynamoDB-record-via-MCP, Writer draft displayed).
   AI-013 + Dashboard are reuse-only (Memory / existing observability).
2. **Human-in-the-loop:** confirmed; **no autonomous send** in the pilot. The hand-off is a **DynamoDB
   record written via the MCP Gateway**; the Writer's draft is displayed for a human to send.
3. **Early-release journey:** confirm **Financial Hardship (FH)** is the first end-to-end vertical.
   *(Assumed yes, per slides 2–3.)*
4. **Rename vs fork:** implement in place in `hesta-claimsagent` (rename "claim" concepts to "case"),
   or keep `claimsagent` untouched as reference and evolve only `hesta-claimsagent`? *(Assumed: evolve
   `hesta-claimsagent`; leave `app/claimsagent` as the original demo.)*
5. **Knowledge source for the Writer (AI-011):** where does "approved HESTA knowledge" live: a curated
   KB / Bedrock Knowledge Base / doc set? Needed before Phase 4.
6. **Attachments in the POC:** samples only carry `[ATTACHMENT FILENAME]` markers (no bytes). Confirm
   AI-004 works on **metadata/markers** for the POC, with real document parsing deferred.
7. **Data handling:** samples are de-identified; confirm production PII handling / retention approach
   before real mailbox integration.
8. **Member data source for AI-003 (§6.1):** for the pilot we seed **synthetic records into the existing
   `PoliciesTable`** (via `seed_dynamodb.py`), meaning no new table. Confirm that's acceptable vs. pointing at a
   read-only HESTA member dataset/API later. *(Assumed: synthetic seed into the existing table.)*
9. **Verification factors & thresholds:** confirm which identifier combinations count as `verified`
   vs `partial` (proposed default: `member_number`+`email`, **or** `name`+`dob`+`address`). The samples
   show HESTA asking for member number + full name + DOB + address, so is that the bar to codify?

---

## Appendix A: Folder ↔ intent quick reference

| Folder | Samples | Intent | Regulated | First-phase target |
|---|---|---|---|---|
| `BDBN/` | 3 | `death_benefit_nomination` | Yes | classify + detect form attachment |
| `BP/` | 2 | `withdrawal_benefit_payment` | Yes | classify + status vs how-to sub-intent |
| `COD/` | 3 | `change_of_details` | No* | classify + field(s) to change |
| `DASP/` | 3 | `departing_australia_payment` | Yes | classify + DASP-vs-BP disambiguation |
| `FH/`  | 3 | `financial_hardship` | **Yes (early release)** | classify + evidence attachment |
| `FLS/` | 1 | `family_law_split` | Yes | classify + solicitor sender-type |
| `NOI/` | 3 | `notice_of_intent_tax_deduction` | Yes | classify + NOI form present |
| `RTC/` | 3 | `rollover_transfer_combine` | No* | classify + member/non-member |

\* still requires identity verification before changes.

## Appendix B: Volumes (from `Cognizant Emails.xlsx` → "Volumes")

~**8,400–8,600 emails/month** total (May: 8,597; June: 8,403), roughly **~4,500 inbound** + ~4,000
outbound per month. This is the scale intent-routing accuracy and AHT reductions apply to: the basis
for the dashboard's cost-to-serve and repeat-enquiry KPIs.
