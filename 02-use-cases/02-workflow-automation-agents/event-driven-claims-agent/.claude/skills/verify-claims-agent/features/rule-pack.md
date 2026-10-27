# Rule packs

A rule pack is a folder holding a plain-language `.feature` file, a `vocabulary.yaml`, and generated `manifest.json` and `compiled/policy.cedar`. An operator compiles it deterministically and the fail-closed loader refuses any pack that is unapproved, edited after approval, or outside its effective dates.

## Sub-features

- `pack-check-refusals` shows the loader refusing each tampered, unapproved or out-of-date variant.
- `pack-check-stands` shows the pack as committed is refused as unapproved.
- `pack-reproduce` shows compiling a copy reproduces the committed manifest and policy byte for byte.

## How to get to it (user POV)

- `python3 rules/cedar_pack.py check clients/<name>/pack`.
- `python3 rules/cedar_pack.py compile clients/<name>/pack`.
- Packs in the repo: `clients/hesta/pack`, `rules/packs/hesta-tool-boundary`, `rules/packs/health-insurer-illustrative`.

## Driving it with control-claims.sh

Preconditions:

- `control-claims.sh setup` has run. The CLI needs `strands[cedar]` and PyYAML, which `demo/.venv` has. System `python3` fails with `No module named 'strands'`.
- No server is needed.

- **Check, default pack.** Run `control-claims.sh pack-check`. The last lines read `21 passed, 0 failed` and `# exit_code: 0`.
- **Check, another pack.** Run `control-claims.sh pack-check rules/packs/health-insurer-illustrative`. The last lines read `19 passed, 0 failed` and exit `0` (as authored; the default pack runs 21 checks, this one 19).
- **The pack as it stands.** In the same output the row `the pack as it stands (unapproved)` reads `refused: no approval record for this version of the feature file and vocabulary`. That is the expected result today: no pack is approved.
- **Tamper rows.** The rows for feature file edited, vocabulary edited, approval for a different file, no approval, not yet effective, expired and `effective_from never set` each read `PASS` with a `refused:` reason.
- **Reproduce.** Run `control-claims.sh pack-reproduce`. The output ends with `IDENTICAL: compile reproduces the committed pack` and exit `0`. A difference prints a `diff -r` listing and exits non-zero.
- **Proof.** `artifacts/<RUN_ID>/pack-check.txt` and `pack-reproduce.txt` hold the command, output and exit code. A second `pack-check` in the same run overwrites the first, so copy the file aside before checking another pack.

## Gotchas

- `check` passing means the **refusals work**, not that the pack is approved. An approved pack would show the last row as loaded, which no pack does today.
- Some rows depend on today's date (`expired`, `not yet effective` print dates). A date-based failure on a later day is a test assumption to read, not an app regression.
- `compile` rewrites `manifest.json` and `compiled/` in the directory you give it. Use `pack-reproduce`, which compiles a scratch copy.
- `pack-check` with a directory argument runs from the project root, so relative paths resolve there.
