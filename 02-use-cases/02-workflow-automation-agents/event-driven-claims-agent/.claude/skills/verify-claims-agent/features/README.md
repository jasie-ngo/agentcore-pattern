# Claims agent verification map

This directory is the maintained source for verifying the user-facing and operator-facing behaviour of the event-driven claims agent. Read this index before driving anything, then use the matching feature file as the recipe.

## Baseline preconditions

- Work from the project root, `event-driven-claims-agent/`. The helper is `.claude/skills/verify-claims-agent/scripts/control-claims.sh` (`control-claims.sh` below).
- `control-claims.sh setup` has run once, so `demo/.venv` and `app/04_hesta_member_email/.venv` exist.
- For the HTTP surface, launch with `control-claims.sh up` on `127.0.0.1:18080`. Set `VERIFY_PORT` to avoid a port that something else owns.
- Run `control-claims.sh doctor` and require: both venvs `ok`, the port owned by our pid, `/ping` Healthy. Read the `aws creds` line and record it.
- Never drive an instance this run did not start.

## Driving conventions

- Start each recipe from a clean checkout state. Rule-pack and assembly recipes work on scratch copies under `.run/<RUN_ID>/`, so the committed pack and `build/` are never written.
- Treat every command as literal, including the quoted sample names.
- The HTTP surface streams server-sent events. Read the `summary.txt` that `invoke` writes, and keep the raw `.sse` beside it.
- Unit tests are separate evidence. Do not cite them as proof that a surface works.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final line. Each artifact holds the command, the output and `# exit_code`.
- Record the feature ID and entry point with every artifact you quote.
- Report an unreachable path with the attempted command and the unmet precondition. The usual one is no AWS credentials.
- Do not report a skipped entry point as verified through a different path.
- When `aws creds` is `none`, a clean run of `invoke` is a **failure-mode** proof (the app starts, lists its agents, and stops at the first model call), not a pipeline proof.

## Features

- [Member-email invocation](./member-email-invocation.md) covers `/ping`, `/invocations`, the agent manifest and the no-credentials failure mode, and lists the full-pipeline checks that are still unproven.
- [Rule packs](./rule-pack.md) covers `check` (tamper, approval and date refusals) and `compile` reproducibility.
- [Routing conformance](./routing-conformance.md) covers the feature file run against the legacy and YAML implementations.
- [Client assembly](./client-assembly.md) covers assembling a client project and validating it with the AgentCore CLI.
