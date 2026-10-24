# SME start here: writing rules the framework can enforce

For compliance and operations people. No coding is needed. You need to be able to say, plainly, what an agent may and
may not do.

## What this does, and what it does not

The framework turns your rules into a **check on the tools an agent may call**. Before an agent uses a tool, the check
asks: is this tool allowed, and are these the allowed values? If not, the tool never runs and the agent is told "denied".

It does **not** control what an agent decides or writes. It cannot say "reply politely", "classify this as hardship" or
"check the document is complete". Those rules can still be written down and tested, but nothing enforces them yet.

## The journey, and who does what

| Step | What happens | Who |
|---|---|---|
| 1 | You provide your rules (a feature file) and your source documents (policies, procedures, sample emails) | **You** |
| 2 | The words in your rules are listed in a **vocabulary** file: every tool, input, allowed value and agent | An engineer drafts it, **you confirm it** |
| 3 | A program (no AI) turns the rules and vocabulary into a policy. If a rule uses a word that is not in the vocabulary, it stops and says which | Automatic |
| 4 | Your example scenarios are run against the real policy: each one must give the allow or deny you wrote | Automatic |
| 5 | You review the rules and the vocabulary and **approve**. Nothing runs without an approval | **You** |
| 6 | The agent starts. It refuses to start if the files changed after approval, or the approval date window has passed | Automatic |

Your source documents stay on the machine. They are not added to the shared code history. Only the finished rules are.

## Writing a rule

A feature file is plain text in three layers: a **Rule** (one sentence in plain words), its **policy** (the statement the
program reads), and **scenarios** (examples that prove the policy does what the sentence says).

```
Rule: The agent never approves or denies a claim

  @policy
  Scenario: Policy
    Given the tool "approve_claim" is never permitted

  @must
  Scenario: Approval is refused
    When the agent calls "approve_claim"
    Then the call is denied
```

### The three policy statements

| Say this | It means |
|---|---|
| `the tool "X" is never permitted` | Always refused |
| `the tool "X" is permitted` | Allowed with any input |
| `the tool "X" is permitted only when A is "v1" and B is "v2"` | Allowed only when every listed input has that exact value |

Anything you do not mention is **refused**. That is deliberate: the agent can only do what you have written down.

### Different permissions for different agents

By default a statement applies to **every agent that uses the pack**. To limit it to one agent, add `for the agent "NAME"`
straight after `permitted` or `never permitted`:

| Say this | It means |
|---|---|
| `the tool "X" is permitted for the agent "A"` | Only agent A may use X |
| `the tool "X" is never permitted for the agent "A"` | Agent A may never use X, even if a general rule allows it for everyone else |
| `the tool "X" is permitted for the agent "A" only when f is "v"` | Only agent A, and only with that value |

Things to know:
- **Agent names come from the vocabulary.** They are listed in its `agents` section. A name that is not listed stops the
  build, and the message lists the ones that are.
- **The agent goes before `only when`**, never after it.
- **An agent that uses the pack is refused everything the pack does not allow it, including its own internal tools.**
  So every tool an agent uses, internal ones too, must be listed in the vocabulary and allowed for that agent, or that
  agent stops working. Ask the engineer for the full list of tools each agent uses before you write the rules.
- **A "never … for the agent" rule beats a general "permitted" rule for that agent only.** The others keep the permission.
- **In an example, say who is calling:** `When the agent "A" calls "X"`. Without a name, the example uses a placeholder agent.

Worked example (invented):

```
@policy
Scenario: Policy
  Given the tool "lookup_member" is permitted for the agent "reader"
  And the tool "open_case" is permitted for the agent "handoff" only when status is "pending_review"

@must
Scenario Outline: Who may read records
  When the agent "<agent>" calls "lookup_member"
  Then the call is <outcome>

  Examples:
    | agent   | outcome |
    | reader  | allowed |
    | handoff | denied  |

@must
Scenario Outline: Who may open a case
  When the agent "<agent>" calls "open_case" with status "<status>"
  Then the call is <outcome>

  Examples:
    | agent   | status         | outcome |
    | handoff | pending_review | allowed |
    | handoff | closed         | denied  |
    | reader  | pending_review | denied  |
```

### What the tags mean

| Tag | Meaning |
|---|---|
| `@policy` | This scenario holds the statement the program turns into a policy |
| `@must` | An example that must come out as written. It is run against the real policy |
| `@confirm` | An inference or assumption that **you** must confirm is intended |

### A worked example with inputs

```
@policy
Scenario: Policy
  Given the tool "add_case_note" is permitted only when visibility is "internal"

@must
Scenario Outline: Note visibility
  When the agent calls "add_case_note" with visibility "<visibility>"
  Then the call is <outcome>

  Examples:
    | visibility | outcome |
    | internal   | allowed |
    | member     | denied  |
```

Always write the failing cases (`denied`) as well as the passing ones. They are the ones that catch mistakes.

## Error messages, in plain words

| You see | It means | What to do |
|---|---|---|
| `unknown tool 'x'` (it lists the known ones) | The tool is not in the vocabulary | Fix the spelling, or ask the engineer to add the tool |
| `no argument 'x'` (it lists the known ones) | The input is not listed for that tool | Same |
| `cannot be 'x'` (it lists the allowed values) | The value is not an allowed one | Use a listed value, or ask for it to be added |
| `not in the policy vocabulary` | The sentence is not one of the three statements | Reword it into one of the three (check `for the agent` comes before `only when`) |
| `unknown agent 'x'` (it lists the known ones) | The agent is not in the vocabulary's agents section | Fix the spelling, or ask for the agent to be added |
| `lists no agents` | You used `for the agent`, but the vocabulary has no agents section | Ask the engineer to add one |
| `no @policy statements` | Nothing tells the program what to enforce | Add a `@policy` scenario |
| `exactly one` feature file | The folder has none or more than one | Keep one |

## What you are approving

You approve **two files together**: the rules and the vocabulary. Change either one later and the approval no longer
applies. It must be approved again.

Be aware of today's limits:
- An engineer records your approval by hand in the pack's `manifest.json`. It is **not signed**, so it could be forged.
  Keep your own record of what you approved and when.
- A rule that came from a document is only as good as your reading of it. Anything tagged `@confirm` is a guess you must
  check, especially where a document names no tool and the engineer had to match its words to a tool.
- Approval covers an effective-from date, and optionally an end date. After the end date the agent will not start.

## Before you hand it over

- [ ] Each rule is one sentence a colleague would agree with.
- [ ] Every rule has a policy and at least one example that should be **denied**.
- [ ] Each rule names its source (document and page or slide). Where there is none, it says "assumption".
- [ ] You have listed the rules you want but cannot write as one of the three statements (for example "reply
      politely"). Do not force them in. Hand them over as a separate list.
- [ ] If different agents need different permissions, each rule says which agent, and you have the engineer's list of
      every tool each agent uses, internal ones included.
- [ ] You have not put real member names, numbers or emails in any example. Use invented ones.

## Where things go

```
clients/<your-organisation>/
  (your documents: anything you like. Kept local, never shared in the code history)
  pack/
    vocabulary.yaml            the dictionary: tools, inputs, values and agents (engineer drafts, you confirm)
    <name>.feature             your rules
```

For the technical side, see `clients/README.md`. A worked example is in
`rules/packs/health-insurer-illustrative/`. It is invented, and is not any insurer's rules.
