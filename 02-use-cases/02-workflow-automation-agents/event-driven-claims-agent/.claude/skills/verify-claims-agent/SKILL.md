---
name: verify-claims-agent
description: Drive and prove the event-driven claims agent (app/04_hesta_member_email, the YAML-first member-email pipeline) the way a user or operator touches it - its HTTP runtime (/ping, /invocations), the rule-pack compiler and loader CLI, the routing conformance runner, and client assembly with `agentcore validate`. Use when a change to app/, rules/, clients/*/pack, scripts/assemble_client.py or agentcore/ needs proof in the real app rather than only unit tests.
---

# Verify the claims agent

Everything here runs offline from this folder. The one surface that is **not** provable without AWS is the model-backed pipeline: Bedrock is called at the first agent step. Check `doctor` before you claim anything about it.

Paths below are relative to the project root (`event-driven-claims-agent/`). The helper is `.claude/skills/verify-claims-agent/scripts/control-claims.sh`, written `control-claims.sh` from here on. Run it from anywhere.

| Surface | What the user or operator touches | Feature file |
|---|---|---|
| Runtime over HTTP | `GET /ping`, `POST /invocations` with a member email | [member-email-invocation](features/member-email-invocation.md) |
| Rule packs | `rules/cedar_pack.py compile` and `check` on `clients/<name>/pack` | [rule-pack](features/rule-pack.md) |
| Routing gate | `rules/run_feature.py rules/hesta_routing_gate.feature` | [routing-conformance](features/routing-conformance.md) |
| Client assembly | `scripts/assemble_client.py` then `agentcore validate` | [client-assembly](features/client-assembly.md) |

Read [features/README.md](features/README.md) first: it has the baseline preconditions and the proof standards. Then follow the matching feature file as the recipe.

## Launch

```bash
control-claims.sh setup     # once: demo/.venv (hash-locked, Python 3.14) and app/04_hesta_member_email/.venv (uv sync --frozen --extra cedar). Needs uv and the network.
control-claims.sh up        # starts the app on 127.0.0.1:18080 and waits for /ping
```

- Ready means `GET /ping` returns `{"status":"Healthy",...}`. That is the same endpoint the container health check uses.
- Needs `uv`, `curl`, `lsof`, `python3`, and `agentcore` (only for client assembly). `docker` is only needed for the container build, which this skill does not drive.
- `up` fails rather than reuse a port it did not start. Set `VERIFY_PORT=<free port>` to run beside another instance.
- The rule-pack and conformance surfaces need no server. Skip `up` for them.

## Doctor

```bash
control-claims.sh doctor
```

Read-only. Run it first whenever anything looks off. It reports the git hash and dirty-path count, both virtualenvs, who owns the port (ours or not), `/ping`, and whether AWS credentials resolve. Exit code is non-zero if a venv is missing or the port belongs to someone else. **Do not drive an instance that `doctor` says is not ours.**

The `aws creds` line decides what you may claim:

- `none`: `/invocations` stops at the first model call. You can prove startup, `/ping`, the agent manifest and the failure mode. You cannot prove routing, drafts or tool calls through the pipeline.
- `PRESENT`: `/invocations` calls Bedrock and bills for it. Say so before running it. With no `AGENTCORE_GATEWAY_URL` set the Gateway tools report the lookup as unavailable and unverified cases escalate to a human, which the app README documents as correct.

## Drive

```bash
control-claims.sh invoke <sample>      # sample is a file in app/04_hesta_member_email/sample_emails/ without .json
control-claims.sh pack-check [dir]     # default dir clients/hesta/pack
control-claims.sh pack-reproduce [dir] # compile a scratch copy, require byte-identical output
control-claims.sh conform              # routing feature file against both implementations
control-claims.sh assemble             # assemble a scratch client project, then agentcore validate
control-claims.sh scripts-plan         # deploy/destroy/Cognito scripts for that client, --plan only, with an aws that fails if called
```

Each command is a literal recipe. The commands and expected output for each feature are in the feature files. Do not substitute `rules/cedar_pack.py compile` on a real pack directory for `pack-reproduce`: compile rewrites the pack's `manifest.json` and `compiled/`.

## Evidence

Every proof command writes to `.claude/skills/verify-claims-agent/artifacts/<RUN_ID>/` (git-ignored). Each `.txt` starts with run id, time, git hash, cwd and the exact command, and ends with `# exit_code: N`. `invoke` also keeps the request body, the raw event stream (`.sse`), the HTTP status and a `summary.txt`.

Proof standards:

- Use the real entry point: the HTTP endpoint, the CLI, the runner. Do not import the modules and call functions, and do not call a test-only helper.
- Keep the action and the result together: the command line and its output are in the same artifact, with the exit code.
- State the AWS credential condition (`doctor` output) next to any `/invocations` proof.
- Never report a surface verified through a different path. If a path is unreachable, report the attempted command and the unmet precondition.
- Unit tests (`uv run --no-project --with pytest --with pyyaml python -m pytest tests/<file>`) are a different layer of evidence. Quote them separately; they do not stand in for driving the app.

## Cleanup

```bash
control-claims.sh down     # stops only the pid this run recorded, and only if it still owns the port
control-claims.sh clean    # down, then removes .run/<RUN_ID> and .run/CURRENT; artifacts/ is kept
```

Never kill by process name. `clean` and `down` keep `artifacts/`. Delete old artifact folders by hand when the user is done with them. The commands that write scratch use `.run/<RUN_ID>/` (assembled project, pack copy, server log), never `build/` and never `clients/`.

`RUN_ID` defaults to a timestamp, and `up` records it in `.run/CURRENT` so later commands in other shells join the same run. Export `RUN_ID` to pin one.

## Helpers

- `scripts/control-claims.sh`: the only helper. Subcommands are listed above; run it with no arguments for the usage line.

## Known limits (verified when this skill was written, 2026-10-26)

- Authored with no AWS credentials. The `/invocations` happy path (agents running, `route_decision` verdict, markdown report, memory, guardrail) was **never observed** by this skill. Its feature file lists what to look for, taken from `app/04_hesta_member_email/README.md`, and marks it unproven.
- The container (`app/04_hesta_member_email/Dockerfile`, arm64, non-root, `/ping`) is not driven here. The project README says it was built and answered `/ping` offline; this skill did not repeat that.
- `deploy.sh`, `destroy.sh` and anything under `scripts/` that touches AWS are out of scope and must not be run as verification.
- Python 3.14 is the tested version for `demo/.venv`.
