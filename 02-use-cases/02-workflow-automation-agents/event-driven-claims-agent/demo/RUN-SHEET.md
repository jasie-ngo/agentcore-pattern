# Run sheet: same core, different rules

Audience: practice leadership. About 10.5 minutes. Offline. Every beat is labelled on screen.

## Set up (once, needs `uv` and the network)

```
bash demo/setup.sh                                   # builds demo/.venv from the hash-locked requirements
demo/.venv/bin/python demo/run_demo.py --fast        # a 5-second check that it all works
demo/.venv/bin/python demo/run_demo.py --weeks N --hours N   # presenting: Enter advances each beat
```

Set the two numbers for the ask yourself. Left unset, the screen shows `[weeks: set before presenting]`.
Fallback if anything fails on the day: `demo/TRANSCRIPT.txt` is a full run, and it is the same text the demo prints.

## Beats

| # | Beat | Time | Label on screen |
|---|---|---|---|
| 1 | The refusal: an unapproved pack and a tampered pack do not load | 0:30 | REAL checks, MOCKED approval |
| 2 | The finding: one feature file, two implementations of an anonymised client's routing, one disagrees | 2:00 | REAL |
| 3 | The pack: feature file and vocabulary compile to Cedar; allowed call runs, denied calls are blocked and the model is told | 3:00 | REAL, MOCKED model, tools, approval |
| 4 | The swap: a health insurer (illustrative), new files only, core unchanged | 3:00 | REAL, ILLUSTRATIVE rules |
| 5 | Not done yet | 1:00 | SLIDE |
| 6 | The ask | 1:00 | SLIDE |

## Real, mocked, not done

| REAL (runs live, nothing faked) | MOCKED or ILLUSTRATIVE (say so aloud) | NOT DONE (say so aloud) |
|---|---|---|
| The loader refuses a pack with no approval, and one whose compiled policy was edited, including a policy and hash edited together | The model is scripted: no LLM, so we show the policy check, not model behaviour | Approvals are not signed: an approval is a JSON record anyone could write |
| The deterministic compiler turns each pack's feature file and vocabulary into Cedar | The tools are stand-ins: no AWS, no Gateway. Cedar runs in-process, not in the AgentCore Policy engine | Nothing delivers a client's pack to a running container (the loader itself now ships in the app's core package) |
| Cedar is evaluated by the real Strands handler inside a real agent loop. A denied tool's code never runs and the model is told | The approval record is a labelled test fixture. No person approved anything | Rules can be scoped per agent, but only one agent is wired to a pack; an agent with a pack is refused every tool its vocabulary does not list |
| The runner executes the routing feature file against the client's two real implementations and reports the disagreement | The health-insurer rules were written by us. They are not any insurer's rules, and no insurer or SME has seen them | Memory is gated on a verified member in the YAML port only, and the legacy app is not; effective dates are checked against today, not the case date |
| The second pack ran through the same compiler, loader and handler. The two core files have the same fingerprint before and after, and the same 19 tamper and approval checks pass | The client's own tool-boundary pack is an unapproved draft derived from documents and code | The routing-gate vocabulary is still the first client's; only the tool boundary is extracted |
| | | No real second client, no deployment (no AWS credentials were used), no health-data threat model |
| | | The composition library is alpha and single-maintainer, and is not locked in the repo (it is locked for this demo only) |
| | | **No measured delivery saving.** There is no baseline, so we claim none |

## Say, and do not say

- Say: "same core, different rules", "evidence, not assurance", "we have shown the interface, not the fit".
- Do not say: faster, cheaper, compliant, deployed, production-ready, or that an insurer's rules are in the demo.
- Do not name the client or any prospect. The filter renames the client on screen; it cannot catch what you say.

## Before presenting

- Commit the new files first, so beat 4's `git status` line is meaningful. Until then the "core unchanged" evidence is the fingerprint line, which holds either way.
- The architecture diagram (`docs/pluggable-rule-pack-agentcore-architecture.drawio`) has been validated but never rendered. Open it in draw.io and look before you use it.
- A screen recording is yours to make. Record `demo/.venv/bin/python demo/run_demo.py` with pauses.
- Python 3.14 is the only version tested.
