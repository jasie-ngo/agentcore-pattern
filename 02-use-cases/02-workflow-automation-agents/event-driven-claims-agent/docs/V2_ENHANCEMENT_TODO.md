# HESTA v2 Enhancement TODO — Real Attachments & Intent Forms

## Purpose

Use this document as the implementation source of instruction for replacing the
v2 pilot's *simulated* attachment handling with **real attachment files**
carried on the inbound email, and for introducing a **blank form per intent**
that the agent encloses when a member has not sent one.

This supersedes the previous v2 enhancement TODO. That earlier set (conversation
history, one-case-per-member reuse, attachment-presence signalling, the bounded
Writer↔Reviewer revision loop, and the disclosure-state controls) is **already
implemented** — see "Already delivered" below. Do not re-implement it.

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

Attachments stop being `[ATTACHMENT FILENAME]` text markers that are merely
*counted*, and become real files parsed off a MIME email, matched against a
known per-intent form template, and checked field-by-field for completeness.

---

## Already delivered (do not redo)

| Capability | Where |
| --- | --- |
| `conversation_history` persisted on the case | `lambdas/case_lookup_creation/handler.py` |
| One case per member, reused across intents & status | `agents/context_manager.py` |
| Attachment presence passed to Writer/Reviewer | `agents/attachment_validation.py` |
| Bounded Writer↔Reviewer revision loop (`MAX_DRAFT_REVISIONS`) | `main.py`, `config.py` |
| Disclosure states + deterministic sensitive-value scan | `agents/disclosure_check.py` |

### Deferred from the previous plan (still open, NOT in this scope)

* Full human-review audit record — `lambdas/schemas/email_review.json` still
  lacks `review_id`, `status`, primary intent, sender type, identity state,
  validation failures, `source_object_id`, processing timestamp and pipeline
  version. Extend it opportunistically where this work already touches the
  payload (see TODO 6), but completing it is not a gate for this set.

---

## Implementation rules

1. Preserve the current v2 safety boundary: the agent drafts email but never
   sends it automatically. An "enclosed" blank form is a *listed enclosure* for
   a HESTA staff member to attach — the agent does not transmit files.
2. Fail closed. If an attachment cannot be decoded, exceeds size limits, or
   cannot be matched to a known form, route to human review rather than
   guessing or silently treating it as valid.
3. Attachment validation is **deterministic Python — no LLM**. It must be
   exactly reproducible and cheap. Do not introduce a model call for form
   matching or field-completeness checking.
4. No new third-party dependencies. MIME parsing uses the Python standard
   library `email` package; form parsing is plain string handling.
5. Never claim to have verified something that was not verified. If a form
   field is present but its *content* was not semantically checked, the notes
   must say so.
6. Keep AWS resource names and environment variables v2-specific.
7. Reuse existing Pydantic models, taxonomy helpers, Gateway helpers, logging,
   and test conventions before adding new abstractions.
8. Surface real errors. Do not silently convert parse, Gateway, DynamoDB, or
   configuration failures into successful-looking results.
9. Add or update focused tests for every behaviour change.
10. Keep all changes surgical and verify the relevant tests before completion.

---

## Design decisions (settled — do not relitigate)

### D1 · Transport: one `.eml` MIME file per email

An inbound email is dropped into `s3://<inbox>/claims-inbox/` as a **single
`.eml` object** containing the body and its attachments as MIME parts.

* One S3 object per email, so the existing EventBridge rule
  (`object.key` prefix `claims-inbox/`) and the existing idempotency key
  (`bucket + key + ETag`) keep working unchanged.
* `.eml` is what a real mail gateway (SES, Microsoft Graph) actually deposits,
  so the parser written here is production shape, not demo scaffolding.
* Built and parsed with the standard library `email` package — approximately
  five lines to build, three to parse.
* Fixtures MUST be generated with `cte="7bit"` on text attachments so the
  attachment body stays **human-readable plaintext** inside the `.eml` rather
  than base64. Fixtures must remain greppable and diffable in git.
* The whole object therefore stays UTF-8 safe, so the Trigger Lambda's existing
  `.decode("utf-8")` does not become a crash surface.

### D2 · Form format: plain-text templates

Forms are plain-text files with a machine-readable header and labelled fields.
No PDF, no JSON. Rationale: zero dependencies, exact deterministic parsing,
trivial to generate a filled copy, and readable in a diff.

Template shape:

```text
HESTA-FORM-ID: BDBN-NOM-V1
HESTA-FORM-NAME: Binding Death Benefit Nomination Form
----------------------------------------------------------
Member name        : ______________________________
Member email       : ______________________________
Member number      : ______________________________
Nomination details : ______________________________
----------------------------------------------------------
```

`HESTA-FORM-ID` is the positive identifier. The validator matches on it rather
than guessing from the filename or from field-label similarity.

### D3 · Four fields per form

Every form carries exactly four fields for this pilot:

| Key | Label | Notes |
| --- | --- | --- |
| `member_name` | Member name | |
| `member_email` | Member email | |
| `member_number` | Member number | Added because identity lookup already keys on it |
| *(intent-specific)* | e.g. "Nomination details" | The "reason for email" field, named per intent |

The fourth field is the per-intent one — "Nomination details" for BDBN,
"Reason for hardship" for FH, and so on.

> **Assumption flagged:** the brief named member name, member email and reason.
> `member_number` is taken as the fourth because `member_lookup` already depends
> on it and a form without it cannot be tied to a member. Say so if you want a
> different fourth field.

### D4 · Every intent gets a form

All eight intents in `intents/taxonomy.py` get a form, including the four that
today declare `expected_attachment="none"` (BP, COD, DASP, RTC). `ADVICE` /
`other_unknown` gets none — those paths already short-circuit to refusal or
human review.

### D5 · The `[ATTACHMENT …]` marker convention is deleted

`_ATTACHMENT_MARKER` in `ingestion/email_normalizer.py` is removed outright, not
kept as a fallback. Sample `.txt` emails that still contain markers will
correctly report **zero** attachments after this change — that is the intended
behaviour, and those samples exercise the missing-attachment path.

---

## Execution order

Build bottom-up so each layer can be tested before the next depends on it:

1. **Form catalog** — templates + registry (no other code depends on it yet)
2. **MIME transport** — Trigger Lambda parses `.eml`, extends the payload
3. **Normalizer** — real `attachments` replace the marker count
4. **Validator** — open attachments, match form, check fields
5. **Writer / Reviewer** — enclose the blank form, ask for specific gaps
6. **Propagation** — case history, HITL record, schemas
7. **Fixtures** — one filled-form `.eml` per intent, plus failure fixtures

---

## TODO 1: Build the per-intent form catalog

### Priority

High — everything else reads from this.

### Objective

Define a blank form for each intent as a data file, plus a registry that maps an
intent to its form, required fields, and fill-in guidance.

### Required implementation

1. Create `app/hesta-claimsagent/forms/` containing:
   * `templates/<intent_code>_<slug>_v1.txt` — one blank template per intent
     (8 files), in the D2 shape with the four D3 fields.
   * `catalog.py` — the registry.
2. `catalog.py` exposes frozen dataclasses and lookups:
   * `FormField(key, label)`
   * `FormSpec(form_id, name, intent_id, template_file, fields, guidance)`
   * `form_for(intent_id) -> FormSpec | None`
   * `spec_by_form_id(form_id) -> FormSpec | None`
   * `blank_template(intent_id) -> str` — reads the template file
   * `guidance_for(intent_id) -> str` — short, member-facing fill instructions
3. Template files must be packaged with the Runtime container. Resolve their
   path relative to the module (`Path(__file__).parent`), never the CWD.
4. Repoint `taxonomy.expected_attachment()` at the catalog so the expected
   document is the form's real name instead of a free-text prose string. Keep
   the existing function signature so callers do not churn.
5. Validate the catalog at import: every intent in `INTENTS` resolves to a
   template file that exists and parses, and every `form_id` is unique. Raise
   loudly on a mismatch — this is a packaging error, not a runtime condition.

### Acceptance criteria

* Each of the 8 intents resolves to exactly one `FormSpec`.
* `other_unknown` and the personal-advice path resolve to `None`.
* Every template file exists, contains a `HESTA-FORM-ID`, and declares exactly
  the four fields its `FormSpec` lists.
* A test asserts catalog/template consistency so a hand-edited template that
  drifts from its spec fails CI.

---

## TODO 2: Carry real attachments from S3 through the Trigger Lambda

### Priority

High

### Objective

Parse the dropped `.eml`, extract the body and each attachment, and pass them to
the Runtime.

### Current behaviour

`lambdas/trigger/handler.py` reads the S3 object, decodes UTF-8, regex-detects
one of three text shapes (HESTA contact form, `From:`/`Subject:` email, JSON),
builds a `prompt` string, and invokes the Runtime. It has no concept of an
attachment.

> This supersedes the earlier plan's "the Trigger Lambda is NOT modified"
> reuse decision. That constraint is deliberately dropped here.

### Required implementation

1. Detect MIME: if the object parses as a multipart MIME message, take the
   `text/plain` body via `msg.get_body(preferencelist=("plain",))` and iterate
   `msg.iter_attachments()`.
2. For each attachment collect `filename`, `content_type`, and decoded `text`.
   Non-text content types are recorded with `text=None` and a reason — never
   guessed at.
3. Extend the Runtime payload with an `attachments` array:

   ```json
   {
     "prompt": "...",
     "source": "...",
     "source_object_id": "s3://bucket/key:etag",
     "idempotency_key": "...",
     "claimant_email": "...",
     "attachments": [
       {"filename": "BDBN-NOM-V1-filled.txt",
        "content_type": "text/plain",
        "text": "...",
        "skipped_reason": null}
     ]
   }
   ```

4. Add bounded size limits as configuration:

   ```text
   MAX_ATTACHMENT_BYTES=262144
   MAX_ATTACHMENTS=5
   ```

   Over-limit attachments are included with `text=null` and a `skipped_reason`,
   never silently dropped and never truncated into something that could parse as
   a valid-looking form.
5. Preserve backward compatibility: a plain `.txt` object keeps its existing
   parsing path and yields `attachments: []`.
6. Wrap the UTF-8 decode so a genuinely binary object produces a clear logged
   error and a human-review-worthy outcome rather than an unhandled
   `UnicodeDecodeError` into the DLQ.
7. Derive `claimant_email` from the MIME `From:` header when present.

### Acceptance criteria

* A `.eml` with one text attachment yields one entry with readable `text`.
* A `.eml` with no attachments yields `attachments: []`.
* A legacy plain `.txt` object still processes exactly as it does today.
* An oversized attachment is reported with a `skipped_reason`, not truncated.
* A binary object produces a handled, logged failure — not an unhandled crash.
* Unit tests cover each branch without requiring AWS credentials.

---

## TODO 3: Make the normalizer carry real attachments

### Priority

High

### Objective

Replace the marker count on `InboundEmail` with the real attachment list.

### Required implementation

1. Add an `Attachment` dataclass: `filename`, `content_type`, `text`,
   `skipped_reason`.
2. Add `attachments: list[Attachment]` to `InboundEmail`.
3. `attachment_count` becomes a derived property (`len(self.attachments)`) so
   existing readers keep working without edits.
4. Delete `_ATTACHMENT_MARKER` and its use (see D5).
5. `normalize_email()` accepts an `attachments` argument from the payload and
   populates the list. Body cleaning (banner, footer, quoted history) is
   unchanged.
6. `main.py::_run_pipeline` reads `payload["attachments"]` and passes it in.

### Acceptance criteria

* A normalized email exposes real filenames and text, not a count of markers.
* An email whose body still contains literal `[ATTACHMENT FILENAME]` text
  reports zero attachments.
* `attachment_count` still returns an int for every existing caller.

---

## TODO 4: Validate attachments against the expected form

### Priority

High

### Objective

Open each attachment, confirm it is the form the intent expects, and confirm its
blank fields have actually been filled in.

### Current behaviour

`agents/attachment_validation.py` compares an expected-document string against a
marker count and returns `missing` / `present` / `not_applicable`. It never
inspects content.

### Required implementation

1. Add a deterministic parser — `forms/parser.py`:
   * `parse_form(text) -> ParsedForm(form_id, fields: dict[str, str])`
     by reading the `HESTA-FORM-ID` header and the `Label : value` lines.
   * `is_blank(value)` — true for empty, whitespace-only, runs of `_` / `-` /
     `.`, or a bracketed placeholder such as `[MEMBER NAME]`.
2. Rewrite `assess()` to, for each attachment with readable text:
   * parse it; unparseable ⇒ `unreadable`;
   * compare `form_id` against `form_for(intent_id).form_id`;
     mismatch ⇒ `wrong_form` naming both the expected and the received form;
   * check every required field; any blank ⇒ `incomplete`, listing the
     **specific unfilled field labels**;
   * all filled ⇒ `valid`.
3. Replace the status vocabulary on `AttachmentAssessment`:

   ```text
   valid | incomplete | wrong_form | unreadable | missing | not_applicable
   ```

4. Add fields to `AttachmentAssessment`: `form_id`, `form_name`,
   `missing_fields: list[str]`, `received_filenames: list[str]`.
5. Keep the existing case-wide memory behaviour from `_previously_provided()`,
   but raise its bar: only a **previously `valid`** form counts as already on
   file. A previously incomplete or wrong form does not.
6. Record the validated status (not just a presence count) on the case history
   entry so the case-wide check stays accurate across turns.
7. Field values are checked for **presence, not correctness**. The notes must
   state that the form's contents were not semantically verified.

### Acceptance criteria

* A correctly filled matching form ⇒ `valid`.
* A matching form with one blank field ⇒ `incomplete`, and that field is named.
* A different intent's form ⇒ `wrong_form`, naming both forms.
* An unparseable or non-text attachment ⇒ `unreadable`, routed to human review.
* No attachment, form expected ⇒ `missing`.
* A previously `valid` form in case history suppresses a repeat request; a
  previously `incomplete` one does not.
* The assessment never asserts that field *contents* were verified.

---

## TODO 5: Enclose the blank form and ask for specific gaps

### Priority

High

### Objective

When a member has not sent a usable form, the draft supplies the blank form and
tells them how to fill it. When they have, the draft never asks again.

### Required implementation

1. Add `enclosures: list[str]` to `DraftEmail` — the form files a HESTA staff
   member must attach before sending. The agent does not transmit files; this
   preserves the safety boundary in rule 1.
2. Extend `_attachment_line()` in `agents/writer.py` to pass the full
   assessment: status, form name, missing field labels, received filenames.
3. Writer behaviour per status:
   * `missing` — name the form, include the fill guidance from the catalog, and
     list the blank template in `enclosures`;
   * `incomplete` — thank them for the form, ask **only** for the specific
     unfilled fields, and re-enclose the blank template;
   * `wrong_form` — explain which form was received and which is needed, and
     enclose the correct blank template;
   * `unreadable` — do not speculate; this path is already escalating to a
     human, so the draft acknowledges receipt without asserting validity;
   * `valid` — confirm receipt and do **not** re-request the document;
   * `not_applicable` — no attachment language at all.
4. Update the Writer system prompt: it must never claim to have read, verified,
   or approved the *contents* of an attachment — only that the expected fields
   were present.
5. Update the Reviewer to flag a draft that requests a document already assessed
   `valid`, or that claims the attachment's contents were verified.
6. Render `enclosures` in the streamed `_fmt_draft()` output so a staff member
   can see what to attach.

### Acceptance criteria

* A `missing` assessment produces a draft naming the form, carrying guidance,
  and listing the blank template as an enclosure.
* An `incomplete` assessment asks only for the named unfilled fields.
* A `valid` assessment produces a draft that does not re-request the document.
* No draft claims an attachment's contents were verified or approved.
* The Reviewer rejects a draft that re-requests an already-valid document.

---

## TODO 6: Propagate the richer assessment through persistence

### Priority

Medium

### Objective

Make the validated form outcome visible to case history and to human reviewers.

### Required implementation

1. Case history entries store the validated `attachment_status`, `form_id`, and
   `missing_fields` alongside the existing `attachments_present` count.
   `attachments_present` stays for backward compatibility with existing rows.
2. Extend `lambdas/schemas/email_review.json` and the `email_review` handler
   with `form_id`, `form_name`, `missing_fields`, and `received_filenames`, and
   widen the documented `attachment_status` enum to the TODO 4 vocabulary.
3. Extend `_write_hitl_record()` in `main.py` to send those fields.
4. Opportunistically add the deferred audit fields listed under "Deferred from
   the previous plan" while this payload is already being touched.
5. Do not store attachment file contents in DynamoDB — metadata only.

### Acceptance criteria

* A reviewer can see which form arrived and exactly which fields were unfilled,
  without reading application logs.
* Existing case rows without the new fields still load without error.
* No attachment bytes are persisted.

---

## TODO 7: Build `.eml` sample fixtures with real attachments

### Priority

Medium

### Objective

Give every intent a realistic end-to-end sample, plus fixtures for each failure
path.

### Required implementation

1. Extend `scripts/generate_sample_emails.py` to emit `.eml` alongside the
   existing `.txt`, using `EmailMessage` with `cte="7bit"` (see D1).
2. For each of the 8 intents, take one existing sample email and produce
   `hesta/sample-emails/eml/<INTENT>_<member>_filled.eml` — the same body, with
   a **correctly filled** copy of that intent's form attached. Fill values must
   match the seed member in `scripts/seed_hesta_members.py` so identity
   verification succeeds.
3. Add failure fixtures (three are enough — they do not need to cover all 8):
   * `*_incomplete.eml` — matching form, one field left blank;
   * `*_wrongform.eml` — a different intent's form attached;
   * `*_noattachment.eml` — body asks about the form, nothing attached.
4. Strip the literal `[ATTACHMENT FILENAME]` lines from the bodies of samples
   converted to `.eml`, since a real part now carries that meaning.
5. Add a README in `hesta/sample-emails/eml/` stating what each fixture proves
   and how to drop one into S3.

### Acceptance criteria

* Eight `.eml` fixtures, one per intent, each with a correctly filled form.
* Three failure fixtures covering incomplete, wrong-form and absent.
* Every fixture's attachment is readable plaintext in the raw `.eml`.
* Filled values match the corresponding seed member.
* Regenerating fixtures is idempotent — re-running the script produces no diff
  beyond the MIME boundary string.

---

## Definition of done

This enhancement set is complete only when:

* all TODOs above are implemented or explicitly marked out of scope;
* the `[ATTACHMENT …]` marker convention is gone from the codebase;
* attachment validation is deterministic, with no LLM call in that path;
* a `.eml` dropped in `claims-inbox/` is parsed, its form matched, and its
  fields checked, end to end;
* a missing or incomplete form produces a draft that encloses the blank form
  and names the specific gaps;
* no draft or record claims to have verified attachment *contents*;
* every form template is packaged with the Runtime and resolves at runtime;
* v2-only unit tests pass, including the new form, MIME, and validator tests;
* legacy plain-`.txt` drops still process without error;
* no outbound email sending or file transmission has been introduced;
* existing v2 deployment configuration remains valid;
* `git diff --check` passes for all changed files.
