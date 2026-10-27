# Member-email invocation

An operator sends a member's email to the running agent over HTTP and gets a stream of events back: the agent system starts, the nine specialists and the coordinator are listed, and the pipeline runs to a draft for HESTA staff to review. Nothing is ever emailed to the member.

## Sub-features

- `ping` reports the app healthy.
- `manifest` lists the agents when a session starts.
- `no-creds` fails visibly and early when AWS credentials are missing.
- `pipeline` runs the nine agents, the deterministic routing gate and the draft (unproven here).
- `samples` accepts each payload in `sample_emails/`.

## How to get to it (user POV)

- `GET http://127.0.0.1:18080/ping`.
- `POST http://127.0.0.1:18080/invocations` with a sample JSON body and a session id header of at least 33 characters. The helper sets the header to `verify-<RUN_ID>-` plus zeros.
- Samples: `contact_form_withdrawal`, `hardship_with_attachment`, `personal_advice_request`.

## Driving it with control-claims.sh

Preconditions:

- `control-claims.sh up` printed `up: http://127.0.0.1:18080` and `doctor` exits 0.
- You have read the `aws creds` line of `doctor`.

- **Health.** Run `curl -fsS http://127.0.0.1:18080/ping`. The body is `{"status":"Healthy","time_of_last_update":<epoch>}`.
- **Invoke a sample.** Run `control-claims.sh invoke personal_advice_request`. It prints `http=200` and a summary, and keeps `invoke-personal_advice_request.{request.json,sse,http,summary.txt}`.
- **Manifest (proven).** The summary line `manifest agents:` lists `intent_identifier`, `context_manager`, `identity_profiler`, `attachment_validator`, `empathy`, `case_status_checker`, `writer`, `reviewer_editor`, `human_handoff`, `coordinator` and `main`. A change that adds or removes an agent in `config.yaml` changes this list.
- **No credentials (proven).** With `aws creds: none`, the summary shows `events: {'session_start': 1, 'agent_start': 1, 'error': 2, 'session_end': 1}` and two `error: NoCredentialsError` lines, with `final text length: 0`. Treat this exact shape as the baseline for the failure mode.
- **Other samples.** Repeat with `contact_form_withdrawal` and `hardship_with_attachment`. Each returns `http=200` and the same event shape as the no-credentials baseline. When this skill was written all three samples were driven with no credentials; the manifest list was read from `personal_advice_request` and `contact_form_withdrawal`.
- **Full pipeline (unproven, needs credentials).** Only when `aws creds: PRESENT` and the user has accepted the Bedrock cost: run `control-claims.sh invoke hardship_with_attachment` and check the `.sse` for tool events from `normalize_email` and `route_decision`, a `session_end` with non-empty text, and no `error` events. Without a Gateway URL, expect the identity and case lookups to report unavailable and the case to escalate to a human. Record anything else you see.
- **Proof.** Keep the `.sse`, `summary.txt`, `.http` and the `doctor` output from the same run.

## Gotchas

- The HTTP status is `200` even when the pipeline failed. The failure is in the `error` events inside the stream. Always read the summary.
- With real credentials the run bills Bedrock and uses `global.anthropic.claude-sonnet-4-6` and a Haiku model. `AGENT_MODEL_ID` and `FAST_MODEL_ID` override them.
- Memory is opened only for a verified member and does nothing without a memory id. The guardrail is skipped without `GUARDRAIL_ID`. Do not claim either is working from this surface.
- `main.py` hard-codes port 8080 under `python main.py`. The helper starts uvicorn on `VERIFY_PORT` instead, which loads the same `app`.
- The app answers `/ping` before any model access, so a healthy `/ping` proves startup only.
