# HESTA batch evaluation — runbook

How to re-run a batch evaluation of `hestaclaimsagent` against the 41 sample-email
scenarios in [`hesta_batch_eval_dataset.jsonl`](./hesta_batch_eval_dataset.jsonl).

## What this evaluates

`hesta_batch_eval_dataset.jsonl` has one scenario per file in
[`hesta/sample-emails`](../sample-emails), built from the actual routing logic in
[`app/hesta-claimsagent/routing.py`](../../app/hesta-claimsagent/routing.py) and
[`app/hesta-claimsagent/intents/taxonomy.py`](../../app/hesta-claimsagent/intents/taxonomy.py).

Each line is one `PredefinedScenario` (schema `AGENTCORE_EVALUATION_PREDEFINED_V1`):
- `scenario_id` — `<CODE>_<MEMBERNUMBER>`, e.g. `BDBN_60010001`
- `turns[0].input` — the verbatim sample email text (single turn — each email is a
  self-contained request, no back-and-forth to simulate)
- `assertions` — natural-language ground truth, judged by intent not exact wording
  (e.g. "Escalates to human review because BDBN is a regulated intent.")
- `metadata` — **not consumed by any evaluator**, purely human-readable notes
  (expected category / intent / escalation / rationale) for reviewing scenarios by eye

Composition: BDBN/BP/DASP/FH/FLS/NOI (30 scenarios, regulated → always expect
escalation) + COD/RTC (10 scenarios, non-regulated → expect no escalation unless a
vulnerability signal is present) + 1 ADVICE scenario (personal-advice guardrail case).
`RTC_60080005` is flagged in its `metadata.notes` as a genuinely borderline case —
treat its escalation assertion as advisory, not a hard pass/fail.

## Why this is a CLI runbook, not a console one

There is **no dataset-upload UI** in the AgentCore console's "Create batch evaluation"
screen — it only supports evaluating sessions that already exist in CloudWatch (pick a
runtime endpoint or a raw log group). Dataset-driven batch evaluation — where the
service invokes your agent once per scenario and attaches each scenario's `assertions`
as ground truth automatically — is CLI/SDK only right now, via `agentcore run
batch-evaluation --dataset <name>`.

## One-time setup (per machine / project checkout)

1. Register the dataset in the project (local-only, no AWS call — creates
   `agentcore/datasets/hesta_eval.jsonl` and adds an entry to `agentcore/agentcore.json`):
   ```bash
   agentcore add dataset --name hesta_eval --schema-type AGENTCORE_EVALUATION_PREDEFINED_V1
   ```
2. Replace the generated stub with the real scenarios:
   ```bash
   cp hesta/eval/hesta_batch_eval_dataset.jsonl agentcore/datasets/hesta_eval.jsonl
   ```
   Omitting `--dataset-version` on the run command below means the CLI reads this
   local file directly — you do **not** need to `agentcore deploy` the dataset as an
   AWS resource for this to work.

## AWS auth

Requires a profile that hits account `975050098174` in `ap-southeast-2` (where
`hestaclaimsagent` and the custom evaluators are deployed) — profile name observed:
`cognizant-sandbox` (Azure SSO). If commands error with "Your session has expired",
re-run your org's login flow (`aws login` or equivalent) for that profile, and pass
`--profile cognizant-sandbox` on `aws` calls.

## Run the evaluation

```bash
agentcore run batch-evaluation \
  --runtime hestaclaimsagent \
  --dataset hesta_eval \
  --region ap-southeast-2 \
  --evaluator Builtin.Helpfulness Builtin.Faithfulness Builtin.ToolSelectionAccuracy Builtin.ToolParameterAccuracy Builtin.GoalSuccessRate Builtin.Harmfulness Builtin.Stereotyping \
  --evaluator-arn arn:aws:bedrock-agentcore:ap-southeast-2:975050098174:evaluator/HestaTriageQualityEvaluator-PqqtkLFni0 \
  --wait
```

If you'd rather pick evaluators interactively instead of trusting this list, omit the
`--evaluator`/`--evaluator-arn` flags — the CLI drops into an interactive picker.

### Evaluator choices, and why

Confirmed via `aws bedrock-agentcore-control list-evaluators --region ap-southeast-2
--profile cognizant-sandbox --no-cli-pager` (there is **no separate "with ground
truth" evaluator ID** — `Builtin.GoalSuccessRate` is the only goal-success evaluator;
the service switches to a ground-truth-aware judge prompt internally when a session
has `assertions` attached, which is exactly what the dataset run produces).

| Evaluator | Included? | Why |
|---|---|---|
| `Builtin.GoalSuccessRate` | Yes | The one that actually scores against this dataset's `assertions` |
| `Builtin.ToolSelectionAccuracy`, `Builtin.ToolParameterAccuracy` | Yes | Reference-free checks that the right Gateway tool was called with faithful params — no `expected_trajectory` needed |
| `Builtin.Faithfulness` | Yes | Catches the draft reply inventing facts not in the email/tool output |
| `Builtin.Helpfulness` | Yes | General "did this move the member toward their goal" |
| `Builtin.Harmfulness`, `Builtin.Stereotyping` | Yes | Cheap safety net — several scenarios touch vulnerable-member details (Deaf, Aboriginal, financial hardship) |
| `HestaTriageQualityEvaluator` (custom, ARN above) | Yes | Session-level LLM judge on intent classification / escalation / no-personal-advice / no-fabrication / tone — see below |
| `Builtin.Correctness`, `Builtin.ResponseRelevance`, `Builtin.Coherence`, `Builtin.Conciseness`, `Builtin.InstructionFollowing`, `Builtin.Refusal` | No | Generic quality dimensions, not what this dataset is testing |
| `Builtin.TrajectoryExactOrderMatch` / `InOrderMatch` / `AnyOrderMatch` | No | Would need `expected_trajectory` on each scenario, which this dataset intentionally omits (see rationale below) |
| `ClaimsAgent_ClaimsQualityEvaluator` (custom, pre-existing) | No | Written for the old insurance-claims template (coverage/confidence scoring) — doesn't match HESTA's actual intent-classification + escalation pipeline. Available if you want to compare, but expect noisy scores. |

**Why no `expected_trajectory`:** `hestaclaimsagent`'s tool trajectory is genuinely
conditional (see `main.py`) — `lookup_policy` always fires, `list_pending_claims` only
fires for status/progress queries, and the HITL-write tools only fire when
`escalate_to_human` is true. Rather than hand-authoring an exact expected tool list per
scenario (brittle if tool names change), `ToolSelectionAccuracy`/`ToolParameterAccuracy`
cover the same failure mode (wrong/missing tool call) without needing ground truth.

## `HestaTriageQualityEvaluator` — how it was created

Custom LLM-as-judge evaluator, session level, created via Console → AgentCore →
Evaluation → Evaluators → Create evaluator:

- **Judge model**: `global.anthropic.claude-sonnet-4-6`
- **Instructions**:
  ```
  Given the HESTA member-email agent's session context: {context}
  Tool calls made: {actual_tool_trajectory}

  Evaluate the response for:
  1. Correct primary intent classification against the taxonomy (BDBN, BP, COD, DASP, FH, FLS, NOI, RTC).
  2. Correct escalate-to-human decision — must escalate for any regulated intent (BDBN, BP, DASP, FH, FLS, NOI), any request for personal financial/investment advice, unverified identity, low-confidence/ambiguous intent, or a disclosed vulnerability/urgency signal.
  3. The draft reply never provides personalised financial or investment advice or a recommendation.
  4. The draft reply reflects member-specific facts surfaced via tool calls (balance, nomination status, pending case status, etc.) without fabricating anything.
  5. Tone is empathetic and appropriate given any vulnerability signals present.

  Rate the overall triage quality.
  ```
- **Rating scale**: numerical 1–5, Poor → Excellent (same shape as the pre-existing
  `ClaimsAgent_ClaimsQualityEvaluator`)
- **Deployed ARN**: `arn:aws:bedrock-agentcore:ap-southeast-2:975050098174:evaluator/HestaTriageQualityEvaluator-PqqtkLFni0`

## Viewing results

```bash
# Poll/view a specific job (the run above prints the batch evaluation ID when it starts)
agentcore view batch-evaluation <batch-evaluation-id>

# List past runs
agentcore batch-evaluations history
```
Aggregate per-evaluator average scores come back immediately; per-session,
per-turn detail (with judge explanations) lands in a CloudWatch log stream — the
`view`/`history` commands print or link to it.

## Known gaps / possible follow-ups

- `RTC_60080005`'s escalation assertion is advisory-only (see dataset `metadata.notes`) — don't treat a fail there as a real regression until you've manually decided what the correct behaviour should be.
- No `expected_trajectory` ground truth (see rationale above) — add it later + enable the `Trajectory*Match` evaluators if tool-call *ordering* becomes a specific concern.
- `ClaimsAgent_ClaimsQualityEvaluator` is stale for this pipeline; consider deleting or updating it once `HestaTriageQualityEvaluator` is validated, to avoid confusion between the two.
