# clients/: one folder per client, kept apart from the core

The core (`rules/cedar_pack.py`, `rules/run_feature.py`, the app) knows nothing about any client. Everything a client
brings lives in its own folder here, and the core finds it only through a setting (`CEDAR_PACK_DIR` points at a `pack/`).
This is deliberate: rules and compliance content are not packaged with the app. Each client plugs in its own.

```
clients/
  _template/            copy this to start a client
    sources/            the client's own documents: policies, procedures, regulator letters
    pack/               vocabulary.yaml and ONE .feature file; manifest.json and compiled/ are generated
  <client-name>/        the same shape
```

## Starting a client

1. Copy `_template/` to `clients/<client-name>/`.
2. Put the client's documents in `sources/`. Their contents are ignored by git (see `.gitignore`): they belong to the
   client and are often confidential. Record which document each rule came from in the feature file's `SOURCES:` header.
3. Write `pack/vocabulary.yaml` and the feature file. The worked example is
   `rules/packs/health-insurer-illustrative/` (its comments explain every field).
4. Compile and check, from the project root:
   `python3 rules/cedar_pack.py compile clients/<client-name>/pack` then `check`.
5. A person with authority approves it. Nothing loads without an approval record (and approvals are not yet signed).
6. Point the agent at it: `CEDAR_PACK_DIR=clients/<client-name>/pack`.

## Rules for this folder

- Nothing in `clients/` is imported by the core. If a client seems to need a core change, log it as a core edit
  (`core-forced` or `core-fix`) rather than working around it here.
- Client names in this folder are real, so they must not appear on a demo screen. Use `demo/anonymise.py` for that.
- The two existing packs under `rules/packs/` were built before this folder existed. They have not been moved,
  because the factory, the tests and the demo reference their paths.
