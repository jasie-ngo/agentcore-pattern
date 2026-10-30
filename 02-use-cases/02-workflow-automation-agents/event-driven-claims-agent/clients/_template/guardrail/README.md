# Guardrail topics for a client

The Bedrock Guardrail denies one thing: **personal advice** (advice tailored to the person asking). What that sounds like is the
client's language, so the wording is a file here, not code. Copy this folder to `clients/<client>/guardrail/`, edit `topics.json`,
and assemble the client:

```bash
python3 scripts/assemble_client.py <client> --display-name "<Client Name>"
```

The assembler copies `clients/<client>/guardrail/topics.json` to `build/<client>/agentcore/guardrail.json`, and the CDK app reads it
when it synthesises. With no file, the client gets the neutral wording in this template.

The file is checked at synth, before any AWS call, and every problem is reported at once. A wrong path or an invalid file stops the
build. It never falls back to other wording.

## The file

```json
{
  "version": 1,
  "description": "Shown in the console. May use {staff}.",
  "blockedInputMessage": "What the member sees when blocked. May use {teamMember} and {staff}.",
  "topics": [
    { "name": "PolicyAdvice", "definition": "...", "examples": ["...", "..."] }
  ]
}
```

| Field | Rule |
|---|---|
| `version` | Must be `1`. |
| `description` | Up to 200 characters. May use `{staff}`. |
| `blockedInputMessage` | Up to 500 characters. May use `{teamMember}` and `{staff}`. |
| `topics` | 1 to 30. Each has a unique `name`, a `definition` and `examples`. |
| `name` | Up to 100 characters: letters, digits, spaces and `_ - ! ? .` only. |
| `definition` | Up to 200 characters. Say what is denied, in the client's terms. No placeholders. |
| `examples` | 1 to 5, each up to 100 characters. Real phrasing a customer would use. No placeholders. |

`{staff}` becomes the client's display name (`--display-name`). `{teamMember}` becomes "a member of the <display name> team". With no
display name they become "the organisation" and "a member of our team". Any other `{word}` is an error.

## What is not configurable

The blocked-output sentinel `[GUARDRAIL_BLOCKED_ADVICE]` is fixed. The Reviewer agent matches it by value, so changing it per client
would silently disable the check.

## Writing one from a client's material (for a person or an agent)

1. Find where the client says what staff may and may not advise on (a compliance manual, a product disclosure statement, a call script).
2. Write one topic per distinct kind of advice they forbid. Prefer one broad topic with five strong examples over many thin ones.
3. Take the examples from real customer phrasing, not from the client's internal terms. Use words a customer would type.
4. Keep the definition about *tailoring to the individual*. General information (what a product is, how a form works) must stay allowed.
5. Do not put a person's details, a policy number or a real claim in any example.
6. Check it: `cd agentcore/cdk && npm run build`, then synthesise with `-c guardrailTopics=<path>` and read the errors, if any.

Bedrock's limits are enforced here so a mistake fails at synth, not mid-deploy. If AWS changes a limit, update `LIMITS` in
`agentcore/cdk/lib/guardrail-topics.ts`.
