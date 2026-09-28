# HESTA v2 Enhancement TODO — Subject-Threaded Cases & Memory-Backed Conversations

## Purpose

Use this document as the implementation source of instruction for two linked
changes to the v2 pilot:

1. **Case lookup** stops assuming one case per member. A case is identified by
   the verified **member id + the email subject** (ignoring `RE:` prefixes), so
   one member can have several concurrent cases, one per email thread.
2. **Conversation storage** moves out of the DynamoDB case item. The email
   conversation (inbound emails, generated drafts, review outcomes) is stored in
   **AgentCore Memory**, in a session whose id is derived from the case id, and
   is read back on every follow-up email to that case.

This supersedes the previous v2 enhancement TODO (real attachments & intent
forms). That set is **already implemented** — see "Already delivered" below. Do
not re-implement it.

Scope is limited to the v2 implementation:

* Deployed AgentCore Runtime: `hestaclaimsagent_v2`
* Runtime source code: `app/hesta-claimsagent`
* Gateway Lambdas: `lambdas/member_lookup`, `lambdas/case_lookup_creation`,
  `lambdas/email_review`
* Trigger Lambda: `lambdas/trigger`
* Sample fixtures: `hesta/sample-emails`, `scripts/generate_sample_emails.py`
* v2 infrastructure: `agentcore/agentcore.json` and `agentcore/cdk`
* v2 tests and configuration

Do not modify the old-version code (`app/claimsagent`, `lambdas/old-version`,
`app/hesta-claimsagent/agents/old-version`) unless a shared interface must be
changed for v2 compatibility. Do not add outbound email sending, production
dashboards, or unrelated features.

### The change in one sentence

A case becomes `(member_id, normalized subject)` instead of `member_id`, and its
email conversation lives in an AgentCore Memory session keyed by that case
instead of in a `conversation_history` list on the DynamoDB item.

---

## Already delivered (do not redo)

| Capability | Where |
| --- | --- |
| Per-intent plain-text form catalog + templates | `forms/catalog.py`, `forms/templates/` |
| `.eml` MIME transport with real attachments | `lambdas/trigger/handler.py` |
| Real `attachments` on `InboundEmail`; marker convention removed | `ingestion/email_normalizer.py` |
| Deterministic form matching + field-completeness validation | `forms/parser.py`, `agents/attachment_validation.py` |
| Writer encloses blank form / asks for specific gaps; Reviewer flags re-requests | `agents/writer.py`, `agents/reviewer_editor.py` |
| Validated form outcome on HITL record | `lambdas/email_review`, `main.py::_write_hitl_record` |
| Bounded Writer↔Reviewer revision loop (`MAX_DRAFT_REVISIONS`) | `main.py`, `config.py` |
| Disclosure states + deterministic sensitive-value scan | `agents/disclosure_check.py` |

---

## Implementation rules

1. Preserve the current v2 safety boundary: the agent drafts email but never
   sends it automatically.
2. Case lookup and case creation stay **deterministic Python — no LLM**. Subject
   normalization and case-id derivation must be exactly reproducible.
3. Only a **verified** member (`member_lookup` returned a `member_id`) gets a
   case or a Memory session. Unverified senders get neither — same as today.
4. Memory stays best-effort at the infrastructure level (`MEMORY_ID` unset or
   Memory unavailable must not crash processing), but the degradation must be
   **visible**: the streamed output states that prior conversation could not be
   loaded. Never silently present a follow-up as a first contact.
5. No new third-party dependencies. Use the existing `bedrock-agentcore` SDK /
   `boto3` already in the Runtime.
6. Keep AWS resource names and environment variables v2-specific.
7. Reuse existing Pydantic models, Gateway helpers, logging, and test
   conventions before adding new abstractions.
8. Surface real errors. Do not silently convert Gateway, DynamoDB, Memory, or
   configuration failures into successful-looking results.
9. Add or update focused tests for every behaviour change.
10. Keep all changes surgical and verify the relevant tests before completion.

---

## Design decisions (settled — do not relitigate)

### D1 · The agent's reply subject is deterministic: `RE: <original subject>`

Subject threading only works if the reply HESTA sends carries the member's
original subject. Today the Writer produces `"HESTA — {intent name}"` or
`"Re: your HESTA enquiry"`, so a member replying to it would arrive with a
subject that no longer matches their case.

* The draft subject is set **in code, after** the Writer runs — never produced
  by the LLM: `RE: <original subject as received, with existing RE:/FW:
  prefixes stripped>`.
* This applies to every draft path, including the personal-advice decline
  (`writer.advice_decline_draft`) and any fallback draft.
* `email_review` already stores `draft_subject`, so staff send the reply with
  the matching subject.

### D2 · No special handling of subject edge cases

Subject normalization strips leading reply/forward prefixes, collapses
whitespace, and casefolds — nothing more. Blank subjects (e.g. contact-form
submissions), translated prefixes, and generic subjects are **not** specially
handled for this POC; a blank subject simply normalizes to an empty thread key
like any other value.

### D3 · Members always reply on the same subject

Follow-ups arrive as `RE: <original subject>`. A member never starts a new
subject for an existing matter, so there is no case re-open, generation counter,
or "closed case → new case" logic. A matching case is reused regardless of its
status.

### D4 · No legacy data — tables are wiped

There are no existing records to preserve. The cases table (and any old Memory
events) are wiped clean before this ships.

* Remove the "one case per member, earliest-created wins" reuse rule and the
  `member_id-index` query from the lookup path entirely — no legacy fallback.
* Remove `conversation_history`, `history_revision`, and the
  backward-compatibility `attachments_present` field from the case item.
* No migration script.

### D5 · Memory retention stays at the default

Keep `eventExpiryDuration` as currently configured in `agentcore/agentcore.json`.
This is a POC; do not tune retention.

### D6 · Memory session ≠ Runtime session

Only the **AgentCore Memory** `sessionId` is reused per case. The **Runtime**
session (`runtimeSessionId` on `InvokeAgentRuntime`) remains one per inbound
email and is **not** changed:

* a Runtime session is a short-lived microVM (idle timeout / max lifetime in
  hours), while follow-ups arrive days later;
* AgentCore Evaluation expects exactly one `invoke_agent` span per Runtime
  session (see `main.py::invoke`) — sharing a Runtime session across emails
  would break the evaluators.

### D7 · Transcript in Memory, case facts on the DynamoDB item

Memory holds the conversation. The DynamoDB case item holds only small case
**state** that must not depend on Memory being available or unexpired — no email
text:

```text
case_id, member_id, thread_key, subject, status, identity_status,
primary_intent_id, last_intent_id, created_at, last_contact_at,
forms_on_file: { "<form_id>": "valid" }
```

`forms_on_file` replaces the scan of `conversation_history` that
`attachment_validation._previously_valid()` does today.

### D8 · Identifiers

* `thread_key = normalize_subject(subject)`
* `case_id = "CASE-" + sha256(f"{member_id}\n{thread_key}")[:20].upper()` —
  deterministic, so concurrent first contacts on the same thread still collide
  on the existing conditional `put_item` and resolve to one case.
* Memory `actor_id = member_id` (sanitized with `_safe_memory_id`).
* Memory `session_id = _safe_memory_id(case_id)` — stable for the life of the
  case.

---

## Execution order

Build bottom-up so each layer can be tested before the next depends on it:

1. **Subject plumbing** — Trigger → payload → normalizer → `thread_key`
2. **Case Lambda** — `(member_id, thread_key)` lookup, new item shape, facts update
3. **Memory conversation store** — append / load helpers
4. **Pipeline wiring** — reorder `main.py` / Context Manager around the lookup
5. **Attachment validation** — read `forms_on_file`
6. **Writer** — deterministic reply subject
7. **Tests & fixtures** — follow-up thread fixtures, unit tests
8. **Docs** — ADR + workflow doc

---

## TODO 1: Carry the email subject end to end

### Priority

High — the case key depends on it.

### Current behaviour

The Trigger Lambda only embeds the subject inside `source` (`"email:<subject>"`).
`main.py::_run_pipeline` calls `normalize_email(...)` without `subject=`, so
`InboundEmail.subject` is always empty.

### Required implementation

1. `lambdas/trigger/handler.py`: add `payload["subject"]` — from the MIME
   `Subject` header for `.eml`, from `parse_email` headers for legacy
   `From:/Subject:` text. Omit (or empty) for contact-form and JSON shapes.
   Leave `source` unchanged.
2. `main.py::_run_pipeline`: pass `subject=payload.get("subject")` to
   `normalize_email`. Accept `subject` in the `agentcore dev` payload shape too.
3. `ingestion/email_normalizer.py`:
   * add `normalize_subject(subject: str | None) -> str` — repeatedly strip
     leading `RE:` / `FW:` / `FWD:` prefixes (case-insensitive, optional
     whitespace), collapse whitespace, casefold;
   * add `thread_key: str` to `InboundEmail`, populated by `normalize_email`.

### Acceptance criteria

* `"Binding nomination"`, `"RE: Binding nomination"`,
  `"Re: RE: binding  nomination"` and `"FW: Binding nomination"` all yield the
  same `thread_key`.
* A `.eml` and a legacy `From:/Subject:` `.txt` both deliver `subject` to the
  Runtime payload.
* Unit tests cover the normalizer and the Trigger payload without AWS
  credentials.

---

## TODO 2: Key cases on member id + thread key

### Priority

High

### Current behaviour

`lambdas/case_lookup_creation/handler.py` queries `member_id-index` and reuses
the member's earliest case; `_case_id(member_id)` hashes the member id only. The
item carries a bounded `conversation_history` list appended via the
`case_id` + `history_entries` route.

### Required implementation

1. Inputs: add `thread_key` (required for lookup/create) and `subject` (display
   only) to the handler and to `lambdas/schemas/case_lookup_creation.json`.
2. `_case_id(member_id, thread_key)` per D8. Lookup is `get_item` on that id;
   not found ⇒ create with the conditional `put_item` (keep the existing
   `ConditionalCheckFailedException` → re-read race handling).
3. Remove the `member_id-index` query, the earliest-case reuse rule,
   `_history_entry`, `_append_history`, `conversation_history`,
   `history_revision`, and `attachments_present` (D4).
4. New item shape per D7.
5. Replace the `history_entries` route with a **case-facts update** route
   (`case_id` + `case_facts`), which:
   * sets `last_intent_id` and `last_contact_at`;
   * merges `forms_on_file` entries (a form once `valid` stays `valid`);
   * uses a single `update_item` (no read-modify-write).
6. Lookup response: `status` (`existing_case_found` | `new_case_created`),
   `case_id`, `case` (the item). Drop `conversation_history` from the response.
7. Keep the `member_id-index` GSI in CDK (harmless); it is simply no longer
   queried by this Lambda. Wipe the cases table before deploy (D4).

### Acceptance criteria

* Same member + `"X"` then `"RE: X"` ⇒ same `case_id`.
* Same member + `"X"` and `"Y"` ⇒ two distinct cases.
* Different members + same subject ⇒ two distinct cases.
* Two concurrent first contacts for the same member + thread ⇒ one case.
* The case item contains no email text.
* A facts update marking a form `valid` is not reverted by a later
  `incomplete` for the same form.

---

## TODO 3: Store the conversation in AgentCore Memory

### Priority

High

### Current behaviour

`memory/session.py::record_interaction` writes one USER/ASSISTANT event per
email into a **new random session** (`hesta-{actor}-{uuid4}`) and nothing ever
reads it back. Context comes from the DynamoDB `conversation_history`.

### Required implementation

1. In `memory/session.py` add:
   * `thread_session_id(case_id) -> str` (D8).
   * `append_turns(actor_id, session_id, entries) -> bool` — one
     `create_event` per entry, in order:
     * inbound member email ⇒ role `USER`;
     * generated draft and review outcome ⇒ role `ASSISTANT` (or `OTHER` for the
       review outcome, if cleaner);
     * carry `entry_id`, `entry_type`, `intent_id`, and — for the inbound entry —
       `attachment_status` / `form_id` as event **metadata**;
     * idempotent on retry: use `entry_id` (`{idempotency_key}:inbound|draft|review`)
       as the event client token, **or** list the session first and skip
       `entry_id`s already present.
   * `load_thread(actor_id, session_id, max_turns=30) -> list[dict]` —
     `list_events` with payloads, sorted oldest→newest, capped, and mapped to the
     **same dict shape** the old `conversation_history` entries had
     (`entry_id`, `entry_type`, `content`, `timestamp`, `intent_id`, …) so the
     Context Manager and Writer prompt rendering stays unchanged.
2. **Verify before building**: confirm the installed `bedrock-agentcore`
   `MemoryClient.create_event` / `list_events` support event metadata and a
   client token. If not, call the `boto3` `bedrock-agentcore` data-plane client
   directly for these two operations.
3. Remove `record_interaction` once `append_turns` replaces it.
4. Leave the SEMANTIC (`claims/{actorId}/facts`) and SUMMARIZATION
   (`claims/{actorId}/{sessionId}`) strategies and namespaces unchanged — with a
   stable session id, the summary namespace now becomes a per-case summary for
   free.

### Acceptance criteria

* Two emails on the same case produce events in the same Memory session, and
  `load_thread` returns them oldest-first.
* Re-processing the same inbound (same idempotency key) does not duplicate
  events.
* `MEMORY_ID` unset ⇒ helpers return empty / `False` without raising.
* Unit tests use a fake Memory client — no AWS credentials needed.

---

## TODO 4: Wire the pipeline around the new lookup order

### Priority

High

### Current behaviour

`main.py::_run_pipeline` derives `actor_id` from the member number or sender
email **before** identity is known, and creates the random session up front.
`context_manager.summarize` runs member/case lookup and the LLM summary in one
call, reading `cases.conversation_history`. History is persisted by
`_append_case_history` in both the normal and personal-advice paths.

### Required implementation

1. Split `agents/context_manager.py`:
   * `lookup(inbound, mcp, ...) -> (IdentityInfo, CaseInfo)` — `member_lookup`
     then `case_lookup_creation` (now sending `thread_key` and `subject`);
   * `summarize(inbound, identity, cases, history, session_manager=None)` —
     the LLM summary, rendering `history` from Memory in place of
     `cases.conversation_history`.
2. In `main.py::_run_pipeline` (and `_run_personal_advice_path`):
   1. `lookup(...)`;
   2. if a case was resolved: `actor_id = member_id`,
      `session_id = thread_session_id(case_id)`, build the session manager, and
      `history = load_thread(...)`;
   3. `summarize(...)` with that history;
   4. pass the same `history` to the Writer where it currently reads
      `summary.cases.conversation_history`.
3. Replace both `_append_case_history` calls with:
   * `append_turns(...)` for the inbound / draft / review entries (same
     `entry_id` scheme as today);
   * a `case_lookup_creation` **case-facts update** (TODO 2.5) carrying
     `last_intent_id` and, when the real assessment is `valid`, the
     `forms_on_file` entry.
4. Remove the up-front random session creation and the separate
   `record_interaction` call.
5. Update the "🧠 Memory" status line to show `actor`, the case-derived
   `session`, the number of prior turns loaded, and whether this turn was
   recorded. If Memory is configured but the load fails, say so explicitly
   (rule 4).
6. Update `CaseInfo` in `models.py`: drop `conversation_history` and
   `cases`/`new_case` duplication as appropriate; add `thread_key`, `subject`,
   `forms_on_file`. Update `_fmt_cases` accordingly.

### Acceptance criteria

* A first email creates a case and writes its turns to the case's Memory
  session.
* A `RE:` follow-up resolves the same case and the Context Manager / Writer
  prompts include the earlier inbound email and draft, loaded from Memory.
* An unverified sender creates no case and writes no Memory events.
* The personal-advice path records its turns to Memory the same way.
* Memory unavailable ⇒ the email is still processed and the output states that
  prior conversation could not be loaded.

---

## TODO 5: Attachment validation reads case facts

### Priority

Medium

### Current behaviour

`attachment_validation._previously_valid(case_history)` scans history entries
for `attachment_status == "valid"`. `main.py` computes an `early_attach`
assessment against an empty history only so the case Lambda can stamp a
brand-new case's bootstrap history entry.

### Required implementation

1. `_previously_valid` reads `forms_on_file` from the case (a `valid` entry for
   the intent's `form_id`, or any `valid` entry if keeping today's case-wide
   behaviour — keep case-wide).
2. `assess(inbound, intent_result, forms_on_file=...)` replaces the
   `case_history` argument.
3. Remove the `early_attach` workaround in `main.py` and the
   `attachment_status` / `form_id` / `missing_fields` / `attachments_present`
   bootstrap inputs to `case_lookup_creation` — facts are written by the
   post-assessment facts update (TODO 4.3).

### Acceptance criteria

* A form assessed `valid` on email 1 suppresses a repeat request on the `RE:`
  follow-up, via `forms_on_file`.
* A previously `incomplete` form does not.
* No attachment validation path reads Memory.

---

## TODO 6: Deterministic reply subject

### Priority

High — without it, follow-ups will not thread (D1).

### Required implementation

1. After the Writer (and after every revision in the Writer↔Reviewer loop), set
   `draft.subject = f"RE: {original subject with prefixes stripped}"`, using the
   original-case subject from `InboundEmail.subject`, not the casefolded
   `thread_key`.
2. Apply the same to `writer.advice_decline_draft` and any fallback draft.
3. Remove subject instructions from the Writer system prompt (it no longer
   decides the subject); the Reviewer must not flag the subject.

### Acceptance criteria

* Inbound subject `"RE: RE: Binding nomination"` ⇒ draft subject
  `"RE: Binding nomination"`.
* `normalize_subject(draft.subject) == inbound.thread_key` for every draft path.
* The HITL record's `draft_subject` carries that value.

---

## TODO 7: Tests and follow-up fixtures

### Priority

Medium

### Required implementation

1. `tests/test_lambda_handlers.py`: rewrite the case Lambda tests for the
   `(member_id, thread_key)` key, the new item shape, the facts-update route,
   and the concurrent-creation race.
2. Normalizer tests for `normalize_subject`; Trigger tests for `subject` in the
   payload; update `tests/test_structured_output.py` and
   `tests/test_dev_payloads.py` for the `CaseInfo` / payload changes.
3. Memory helper tests with a fake client (ordering, idempotency, disabled).
4. Fixtures: extend `scripts/generate_sample_emails.py` to produce at least one
   **thread pair** in `hesta/sample-emails/eml/` — `<INTENT>_<member>_thread_1.eml`
   (original subject, form missing) and `<INTENT>_<member>_thread_2.eml`
   (`RE:` subject, filled form attached) — plus one pair for the same member on
   a **different** subject to prove two cases. Update the fixtures README with
   the drop order.

### Acceptance criteria

* Dropping `thread_1` then `thread_2` into `claims-inbox/` resolves one case,
  and the second draft reflects the first exchange.
* Dropping the different-subject fixture for the same member creates a second
  case.
* v2 unit tests pass without AWS credentials.

---

## TODO 8: Documentation

### Priority

Low

### Required implementation

1. Add `docs/decisions/0015-subject-threaded-cases-memory-conversation.md`
   recording D1–D8, and mark the one-case-per-member behaviour as superseded.
2. Update `docs/HESTA_V2_WORKFLOW.md` and `docs/ARCHITECTURE.md` where they
   describe case lookup and `conversation_history`.

---

## Definition of done

This enhancement set is complete only when:

* all TODOs above are implemented or explicitly marked out of scope;
* a case is resolved by `(member_id, normalized subject)`; the one-case-per-member
  rule and `member_id-index` lookup are gone from the case Lambda;
* no email text is stored on the DynamoDB case item; `conversation_history` is
  gone from the codebase;
* each case's conversation is written to and read from a stable AgentCore Memory
  session derived from its `case_id`;
* the Runtime session remains one per inbound email;
* every draft subject is `RE: <original subject>`, set in code;
* a `RE:` follow-up threads to the original case end to end, with prior turns in
  the agent's context;
* Memory unavailability is handled and visibly reported, never silent;
* v2-only unit tests pass, including the new subject, case, and Memory tests;
* no outbound email sending has been introduced;
* existing v2 deployment configuration remains valid;
* `git diff --check` passes for all changed files.
