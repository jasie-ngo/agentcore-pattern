# ADR-0015: Subject-Threaded Cases + Memory-Backed Conversation Storage

## Status

Accepted — supersedes the "one case per member" rule from the original v2 pilot.

## Context

The v2 pilot originally gave each verified member exactly one case for the life of
the relationship: `case_lookup_creation` queried the `member_id-index` GSI and
reused the member's earliest case regardless of topic or status. The case's
conversation (every inbound email, generated draft, and review outcome) was
stored as a bounded list (`conversation_history`) directly on the DynamoDB case
item, appended via a read-modify-write `update_item`.

Two problems surfaced:

1. **One thread per member is too coarse.** A member who emails HESTA about a
   beneficiary nomination and, separately, about a change of address gets ONE
   case with both topics interleaved in the same history — the Writer has to
   sort out which prior turns are relevant to the current message.
2. **Growing conversation history on a hot DynamoDB item** competes with the
   item's own read/write throughput, is bounded arbitrarily (30 entries,
   8,000 bytes each), and duplicates a use case AgentCore Memory already
   exists for.

## Decision

A case is now identified by **verified member id + normalized email subject**
(`thread_key`), not member id alone — so a member can have several concurrent
cases, one per email thread. Subject normalization
(`ingestion/email_normalizer.py::normalize_subject`) strips leading `RE:`/`FW:`/
`FWD:` prefixes, collapses whitespace, and casefolds; nothing more.

```text
case_id = "CASE-" + sha256(f"{member_id}\n{thread_key}")[:20].upper()
```

deterministic, so two concurrent first-contacts on the same thread still
collide on the same conditional `put_item` and resolve to one case, exactly
like the old per-member scheme did.

The case's conversation moves out of DynamoDB entirely and into an **AgentCore
Memory session**, keyed by the case id:

```text
actor_id   = safe_memory_id(member_id)
session_id = safe_memory_id(case_id)     # thread_session_id()
```

`memory/session.py::append_turns` writes one Memory event per turn (inbound
email → `USER`, generated draft → `ASSISTANT`, review outcome → `OTHER`), with
`entry_id`/`entry_type`/`intent_id`/attachment fields carried as event
metadata; `load_thread` reads them back oldest-first, in the same dict shape
the old `conversation_history` entries had, so the Context Manager and Writer
prompt rendering did not need to change.

The DynamoDB case item now holds only small state that must not depend on
Memory being available or unexpired:

```text
case_id, member_id, thread_key, subject, status, identity_status,
primary_intent_id, last_intent_id, created_at, last_contact_at,
forms_on_file: { "<form_id>": "valid" | "received_not_valid" }
```

`forms_on_file` replaces the scan of `conversation_history` that attachment
validation's "was a valid form already provided?" check used to do — a form
entry, once recorded `valid`, is never reverted by a later update for the
same form. A form that arrives but isn't complete is tagged
`received_not_valid` (staff visibility only; `_previously_valid()` only ever
looks for `valid`) rather than being written with whatever specific status
string produced it (`incomplete`, `wrong_form`, …) — there's no need to
persist that level of detail here, the full assessment is already on the
email_review record when it matters.

Each `update_item` call in `case_lookup_creation::_update_case_facts` writes
at most one status per form: `valid` entries are unconditional (idempotent —
writing "valid" over "valid" is a no-op) and bundled into the same call as
`last_intent_id`/`last_contact_at`; a `received_not_valid` entry is its own
small, separately-conditioned `update_item`
(`attribute_not_exists(...) OR forms_on_file.#fid <> :valid`) so it can never
downgrade a form that's already `valid`, and a stale write there can never
block the primary update from landing. None of this needs a prior `get_item` —
every check is a native DynamoDB condition evaluated at write time.

### The Runtime session stays one per email

Only the **Memory** session is reused across a case's whole life. The
**Runtime** session (`runtimeSessionId` on `InvokeAgentRuntime`) remains one
per inbound email:

- a Runtime session is a short-lived microVM (idle timeout / max lifetime in
  hours), while follow-ups on the same case can arrive days later;
- AgentCore Evaluation expects exactly one `invoke_agent` span per Runtime
  session (`main.py::invoke`) — sharing a Runtime session across emails would
  break the evaluators.

### Idempotency without a client token

The installed `bedrock-agentcore` SDK's `MemoryClient.create_event` does not
accept a client token (the underlying `CreateEvent` API does, but the wrapper
does not expose it). Rather than drop to the raw `boto3` data-plane client for
one parameter, `append_turns` lists the session's existing events first and
skips any `entry_id` already present — the same effect, one extra `list_events`
call per invocation instead of a server-side dedup token.

## Alternatives Rejected

1. **Keep one case per member, add a `thread_id` sub-key inside it.** Would
   need a nested history-per-thread structure on the same DynamoDB item,
   reintroducing the same read-modify-write growth problem this ADR is trying
   to eliminate, just one level deeper.
2. **Derive `case_id` from `member_id` + `primary_intent_id` instead of
   subject.** Two genuinely separate BDBN requests months apart would
   incorrectly collide into one case; subject is what a member and HESTA staff
   actually use to recognise "this is the same conversation."
3. **Use the Runtime session as the Memory session too.** Rejected by D6 above
   — breaks AgentCore Evaluation's one-span-per-session assumption and does
   not survive the Runtime's idle/max-lifetime limits between follow-ups.
4. **Client-token idempotency via the raw boto3 `bedrock-agentcore` data-plane
   client.** Works, but adds a second Memory client code path (wrapper + raw)
   for one parameter; list-then-skip was simpler and needed no new dependency.

## Consequences

- **Case identity changed:** the `member_id-index` GSI query and "earliest
  case wins" reuse rule are gone from `case_lookup_creation`. The GSI itself
  stays in CDK (harmless, simply unused by this Lambda) rather than being torn
  out.
- **No email text on the case item:** `conversation_history`,
  `history_revision`, and the legacy `attachments_present` bootstrap field are
  gone from the case Lambda and the item shape.
- **Memory retention now bounds the conversation:** `eventExpiryDuration` in
  `agentcore/agentcore.json` (left at its existing default — this is a POC,
  not tuned here) is what eventually ages out a case's Memory events; the
  DynamoDB case item persists independently.
- **Visible degradation:** Memory is still best-effort at the infrastructure
  level (`MEMORY_ID` unset never crashes processing), but a *configured*
  Memory that fails to load now raises inside `load_thread` so the caller
  (`main.py`) can surface "prior conversation could not be loaded" instead of
  silently treating a follow-up as a first contact.
- **Deterministic reply subject (D1 companion decision):** since a member's
  reply threads back to the same case only if their reply carries a subject
  that still normalizes the same way, every draft's subject is now set in
  code — `RE: <original subject, prefixes stripped>` — never left to the
  Writer LLM.
- **No migration:** there is no legacy data to preserve for this POC; the
  cases table (and any old Memory events) are wiped before this ships.
