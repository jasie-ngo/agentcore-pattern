# Routing conformance

An operator runs the routing-gate feature file against both pilot implementations, the legacy app and the YAML port, and reads whether they agree with the rules written in plain language.

## Sub-features

- `conform-run` runs every scenario against both implementations and prints a verdict per implementation.
- `conform-drift` reports where an implementation disagrees with the feature file.
- `conform-confirm` lists the scenarios still awaiting SME confirmation.

## How to get to it (user POV)

- `python3 rules/run_feature.py rules/hesta_routing_gate.feature`.

## Driving it with control-claims.sh

Preconditions:

- `control-claims.sh setup` has run. The runner imports both apps and needs `demo/.venv`. System `python3` prints `UNAVAILABLE ... No module named 'pydantic'` and `nothing was checked`, which is not a pass.
- No server is needed.

- **Run.** Run `control-claims.sh conform`. The output starts with `Feature : hesta_routing_gate.feature`, shows the two `Adapter :` lines, then one summary row per implementation, ends with `RESULT: OK` and `# exit_code: 0`.
- **Per-implementation rows.** As authored: `legacy PASS 36 FAIL 0 DRIFT 0 NA 12 SKIP 9 ERROR 0` and `yaml_port PASS 47 FAIL 0 DRIFT 1 NA 0 SKIP 9 ERROR 0`. Compare the numbers to those, not just to `RESULT: OK`.
- **Drift.** The `DRIFT 1` on `yaml_port` is a known, reported disagreement. A new `DRIFT` or any `FAIL` or `ERROR` means a behaviour change.
- **Awaiting confirmation.** The section `Awaiting SME confirmation (@confirm): 7` lists seven scenarios with file line numbers. These are draft rules no SME has confirmed.
- **Proof.** `artifacts/<RUN_ID>/conform-routing-gate.txt`.

## Gotchas

- `RESULT: OK` can appear alongside `DRIFT`. Read the per-implementation counts.
- If an adapter prints `UNAVAILABLE`, the run checked nothing for that implementation. Report it as unverified.
- `NA` rows on `legacy` are steps the legacy code cannot expose without a live Gateway. They are not failures.
- The feature file's status is `DRAFT 0.1 - generated from code, NOT yet approved`. Passing proves the code matches a draft written from the code.
