# A client's pipeline overlay

The member-email pipeline is two layers, merged when a client is assembled:

| Layer | Where | Holds |
|---|---|---|
| Neutral base | `app/04_hesta_member_email/core/pipeline.base.yaml` | The models and their pinned defaults, and the variables `primary_model`, `fast_model`, `guardrail_sentinel`, `profile`. Same for every client. |
| Client overlay | `clients/<client>/pipeline/pipeline.yaml` | The client's `package`, `agents` and their prompts, `orchestrations`, `entry`, and optionally extra `vars` and new model aliases. |

```bash
python3 scripts/assemble_client.py <client> --display-name "<Client Name>"
```

The assembler writes `build/<client>/app/04_hesta_member_email/config.yaml` (generated: edit the overlay, never that file). It ships
the core and **only this client's package**. No other client's code, sample emails or wording is in the image.

## What the overlay may and may not do

- It **may** add variables and new model aliases.
- It **may not** redefine a base variable or model alias. That is how the pinned models and the guardrail sentinel stay pinned.
- Agents choose a model by alias (`primary`, `fast`, `guarded`), never by `model_id`.
- Every `type:` and `tools:` path must start `./<package>/` or `./core/`.
- **It may not name another client anywhere in its text.** A prompt copied from another client's overlay and not rewritten is refused,
  with the offending name in the message.
- `entry`, `entry_name` and every connection must refer to things that exist.

The build checks all of this before it copies anything, and reports every problem at once.

## The package comes first

`package` names a folder beside `core/` that holds the client's tools, schemas and factory. It must contain `factory.py`. Write it
as a sibling of `hesta/`, never an edit to `core/` (`tests/test_core_is_client_neutral.py` enforces that the core names no client).
The template's `package: your_client_package` stops the build until you replace it.

## With no overlay

A client with no `pipeline/pipeline.yaml` still builds, but the image then holds the **first client's** whole app folder, agents and
prompts included. The build prints a warning and says so in `README-BUILD.md`. Treat that as a placeholder, not a deliverable.

## Not yet covered

The checks are structural plus strands-compose's own schema validation. Building every agent from a non-first-client overlay is only
possible once that client's package exists, so it is part of writing the package, not of this folder.
