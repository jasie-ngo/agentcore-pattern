# Illustrative insurer: pre-sale demo data

**Everything here is synthetic.** The company, the policyholders, the policy numbers, the addresses and the emails are all invented, and
the email addresses use the reserved `example.com`, `example.net` and `example.org` domains. This is **not a real insurer's data or
rules**. A pre-sale deployment holds synthetic data only (ADR-0015): nothing in the code can tell real data from invented data, so that
is a rule, not a check. `scripts/seed_policyholders.py` does refuse any record that is not marked synthetic or that has a non-example email.

## What is here

| What | Where |
|---|---|
| 14 synthetic policyholders and 2 pending claims | `policyholders.json` |
| 15 sample emails (14 emails and 1 web form) | `emails/` |
| What the pipeline should do with each | `scenarios.json` |
| The rules for the hand-off agent's tools (a Cedar pack) | `../pack/` |
| The personal-advice and promised-outcome guardrail wording | `../guardrail/topics.json` |

## Running it

Nothing below has been run against AWS yet. Offline, `--list` and `--dry-run` work and the tests run every email through the checks.

```bash
# 1. Build this client's own project (presale profile: synthetic data only)
python3 scripts/assemble_client.py fininsurer --display-name "Illustrative Insurer" --profile presale

# 2. See every name it would use, and change nothing
./deploy.sh ap-southeast-2 --client fininsurer --plan

# 3. After a deploy: load the synthetic policyholders (--reset first removes earlier synthetic records)
python3 scripts/seed_policyholders.py --client fininsurer --list                      # what would be loaded, no AWS
python3 scripts/seed_policyholders.py --client fininsurer --region ap-southeast-2 --reset

# 4. Drop a sample email into this client's inbox bucket (its name is in the --plan output)
aws s3 cp clients/fininsurer/demo/emails/02_hardship_premium_job_loss.txt s3://<inbox-bucket>/claims-inbox/
```

## The Cedar pack is a draft: unapproved on purpose

`../pack/` compiles and its 16 example rules pass against the real Cedar handler (`python3 rules/cedar_pack.py check clients/fininsurer/pack`).
It has **no approval record**, so the agent will not load it, and `assemble_client.py --enable-pack` is refused. An approval has to come from
the insurer's compliance reviewer after they confirm each `@confirm` rule, and recording one by hand would be forging it. To show the
rules enforcing live in a demo, use the labelled test fixture in `rules/cedar_pack.py` on a temporary copy, and say so on screen.

## The emails

The first two columns come from `scenarios.json`. "Needs the classifier" means the message text alone would not escalate the case: it is
escalated only because the intent identifier sets a flag, which is an LLM judgement. The tests do not cover those judgements.

| Email | Policy | What it shows | Outcome | Needs the classifier |
|---|---|---|---|---|
| `01_claim_status_routine` | POL-10001 | The light path | Verified, no escalation | n/a |
| `02_hardship_premium_job_loss` | POL-10002 | Hardship, policy in arrears still verifies, evidence attached | Escalated, high priority | No |
| `03_hardship_claim_support` | POL-10003 | Hardship on a pending claim, no hardship wording | Escalated, high priority | **Yes** |
| `04_hardship_lapsed_gentle` | POL-10004 | Understated distress, lapsed policy still verifies | Escalated, high priority | **Yes** |
| `05_cancellation_instruction` | POL-10005 | An instruction to cancel | Escalated | No |
| `06_policy_enquiry_routine` | POL-10010 | A second light-path case | Verified, no escalation | n/a |
| `07_complaint_claim_handling` | POL-10006 | A formal complaint | Escalated to complaints | **Yes** |
| `08_takeover_email_change` | POL-10007 | Email change and password reset from a different address | Not verified, escalated | No |
| `09_vulnerable_family_violence` | POL-10008 | Hardship with a safety concern | Escalated, urgent | No |
| `10_third_party_counsellor` | POL-10011 | A financial counsellor writing for a customer | Not verified, escalated | No |
| `11_claim_lodgement_storm` | POL-10012 | A new claim with photos | Escalated (a person decides every claim) | **Yes** |
| `12_unverified_no_policy_number` | none | Nothing to verify | Unverified, escalated | n/a |
| `13_cancelled_policy_status` | POL-10009 | A cancelled policy is capped at partial | Escalated | n/a |
| `14_personal_advice_request` | POL-10013 | Asks what to do about cover | Advice declined, escalated | No |
| `15_web_form_hardship` | POL-10014 | The web-form input shape | Escalated, high priority | No |

Under the strict profile every matching sender is capped at "partial", because nothing sets the email-authentication signal yet (see
AGENTS.md, invariant 18). The presale profile accepts the address match. That is why `verification_presale` and `verification_strict`
differ in `scenarios.json`.

## What this does not prove

- It does not prove the intent identifier, the empathy agent, the writer or the reviewer behave as described. Those are model
  behaviours and need an evaluation against real wording and a real insurer's policy.
- Attachments are counted, not read. A person always reviews hardship evidence.
- The hardship wording and the rules are made up. An insurer's own hardship policy, code-of-practice obligations and approved text
  must replace them before any real use.
