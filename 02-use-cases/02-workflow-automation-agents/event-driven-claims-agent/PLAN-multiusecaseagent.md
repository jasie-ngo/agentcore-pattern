# Plan: Multi-use-case agent (HESTA + Claims, one shared design)

Status: **planning only, nothing executed yet.**

## Goal

One design that serves two use cases (HESTA member-email, insurance claims),
where:
- a use-case-specific change is made inside that use case's own folder,
- a framework/plumbing change is made in one shared folder, affecting both,
- switching which use case is deployed is a single one-line edit,
- `agentcore.json` is configured once and never touched again for any of the above.

## Final folder layout

```
app/
├── main.py                 # top-level, fixed entrypoint, never changes
├── _shared/                 # SOURCE OF TRUTH — generic, no domain logic, referenced directly (no copies)
│   ├── gateway.py            # MCP Gateway client (Cognito M2M)
│   ├── memory.py             # AgentCore Memory session manager + local fallback
│   ├── factory.py            # agent builder: structured_output + gateway attach
│   └── settings.py           # only the truly generic env vars (GATEWAY_URL, REGION, etc.)
│
├── hesta_agent/               # was 04_hesta_member_email, flattened (no nested hesta/ subfolder)
│   ├── config.yaml
│   ├── schemas.py
│   ├── tools.py
│   ├── taxonomy.py
│   ├── knowledge.py
│   ├── ingestion.py
│   └── settings.py            # HESTA-only values (intent threshold, guardrail sentinel)
│
└── claims_agent/               # new, flat, same convention
    ├── config.yaml
    ├── schemas.py
    └── tools.py

lambdas/   ← unchanged, flat, no shared/per-use-case split
```

Note: `main.py` moved **out of** the use-case folders, up to `app/main.py`.
Neither `hesta_agent/` nor `claims_agent/` has its own `main.py` anymore.

## How referencing works (confirmed from the existing code)

- `agentcore.json` → `runtimes[0].codeLocation` = `"app"` (the whole folder,
  not a use-case subfolder). This is what Docker copies into the container
  (`COPY . /app` in the Dockerfile) — fixed, set once.
- `agentcore.json` → `runtimes[0].entrypoint` = `"main.py"` — fixed, set once.
- `app/main.py` contains exactly one line of real logic:
  ```python
  from pathlib import Path
  from strands_compose_agentcore import create_app

  app = create_app(Path(__file__).parent / "hesta_agent" / "config.yaml")
  # change "hesta_agent" to "claims_agent" to switch use case
  ```
  This is the **only line that changes** when switching use cases.
- Because `codeLocation` is the whole `app/` folder, `_shared/` sits inside the
  same Docker build context as both use cases — `hesta_agent/config.yaml` and
  `claims_agent/config.yaml` can both reference it directly, e.g.
  `type: ../_shared/factory.py:agent`. **No copying, no sync script, no
  duplication.** (This supersedes an earlier version of this plan that proposed
  copying `_shared/` into each use-case folder — not needed once `main.py` moved
  to the top level.)

## Renames (remove "hesta" from anything generic)

| Current | New |
|---|---|
| `hesta/factory.py:hesta_agent` | `_shared/factory.py:agent` |
| `hesta/memory.py:hesta_session_manager` | `_shared/memory.py:session_manager` |
| `hesta/gateway.py` docstrings/comments naming HESTA specifically | reworded to be use-case-neutral |
| `hesta/settings.py` (mixed generic + HESTA-specific) | split: generic → `_shared/settings.py`, HESTA-only (`INTENT_CONFIDENCE_THRESHOLD`, `GUARDRAIL_BLOCK_SENTINEL`) → `hesta_agent/settings.py` |

No change needed to `GATEWAY_TOOL_NAMES` — confirmed identical for both use cases already.

## AgentCore Runtime (`agentcore/agentcore.json`)

**One runtime, configured once, never edited again to switch use cases.**

Current state (before this plan): `"name": "hestaclaimsagent"`,
`codeLocation: "app/hesta-claimsagent"`.

Target state (set once, permanent):
```json
"runtimes": [
  {
    "name": "multiusecaseagent",
    "codeLocation": "app",
    "entrypoint": "main.py"
  }
]
```

Also update `onlineEvalConfigs[0].agent` from `"hestaclaimsagent"` to
`"multiusecaseagent"`.

**The only exception** where `agentcore.json` would need a future edit: adding a
genuinely new AWS resource (a new Lambda tool, a new Gateway target, a new
Memory namespace strategy) — that's infrastructure, not a use-case switch, and
applies regardless of this design.

See `SWITCHING_USE_CASES.md` for the switch procedure itself.

## Remove (superseded by this design)

- `app/hesta-claimsagent/` — old hand-written HESTA implementation
- `app/claimsagent/` — old hand-written claims implementation
- `app/04_hesta_member_email/` — folded into `app/hesta_agent/`

Only `app/main.py`, `app/_shared/`, `app/hesta_agent/`, `app/claims_agent/`
remain under `app/`.

## Lambdas / Gateway / Memory (AWS infra)

No change needed — both use cases already point at the same Lambdas
(`create_claim`, `policy_lookup`, `human_review`, `notification`,
`list_pending_claims`, `resolve_claim`), same Gateway, same Memory resource.
Namespace prefixes differ per use case via `config.yaml` params only.

## Phase 3 risk — resolved

Option (b) spiked and written: `app/claims_agent/tools.py`'s
`execute_claim_decision` is one `@tool` that does the REJECT/AUTO_APPROVE/
HUMAN_REVIEW branching and calls the Gateway tools directly, no extra LLM
call. It depends on `app/_shared/gateway.py`, which doesn't exist until step 1
below is done — not yet runnable, but the pattern is proven out.

## Build order

1. Create `app/main.py` at the top level (the 3-ish line loader shown above,
   defaulted to `hesta_agent`). Move `gateway.py`/`memory.py`/`factory.py`/
   `settings.py` out of `app/04_hesta_member_email/hesta/` into `app/_shared/`
   as the source of truth; apply the renames above.
2. Move the rest of `app/04_hesta_member_email/` → `app/hesta_agent/`
   (flattened, no nested `hesta/` subfolder, no `main.py` of its own). Update
   `config.yaml`'s `type:`/`tools:` paths to point at `../_shared/...`.
   Confirm it still runs locally (`sca dev --config app/hesta_agent/config.yaml`,
   or via `app/main.py` directly).
3. Build `app/claims_agent/` (`config.yaml`, `schemas.py`, `tools.py`) against
   `_shared/` the same way. Resolve the Phase 3 risk here.
4. Update `agentcore.json`: rename the runtime, set `codeLocation: "app"`,
   `entrypoint: "main.py"`, update `onlineEvalConfigs`.
5. Delete `app/hesta-claimsagent/` and `app/claimsagent/`.
6. Write `SWITCHING_USE_CASES.md` (describes editing `app/main.py`'s one path
   line, not `agentcore.json`).
7. Deploy, verify HESTA works end to end. To test claims: edit `app/main.py`'s
   path to `claims_agent`, redeploy, verify with a sample claim payload.
