# Card-dispute triage (Harbour Bank — fictional)

> The third use case, built **only from framework blueprints**: no agent prompts
> written from scratch, no `schemas.py`, no Lambda code, no decision code.
> It was built first to prove the blueprints; HESTA and claims were then moved
> onto the same blueprints.

A customer emails about a card charge they don't recognise. The pipeline
identifies the customer, finds the transaction, decides what happens, records a
dispute and drafts a reply for a disputes officer. Nothing is sent to the
customer.

## The pipeline

```
coordinator  (hand-written flow; tools: normalize_email, decide_dispute, Gateway writes)
  │
  ├─ 1 UNDERSTAND
  │    intent_classifier   classifier blueprint  → card_dispute | lost_or_stolen_card | fee_query | other
  │    dispute_extractor   extractor blueprint   → amount, merchant, date, card last-4, customer no.
  │
  ├─ 2 DECIDE
  │    dispute_assessor    assessor blueprint    ──▶ Gateway: lookup_customer, list_customer_transactions
  │                                               → valid_dispute | likely_genuine_purchase | needs_information | customer_not_found
  │    dispute_reviewer    reviewer blueprint    → 4 checks, approved true/false
  │    decide_dispute      decision table (no model) → outcome + escalate
  │
  └─ 3 EXECUTE
       create_dispute            Gateway, record_write  (one dispute per customer + transaction)
       request_dispute_review    Gateway, record_write  (only when the decision escalates)
       reply_writer              writer blueprint       → draft reply (NOT sent)
```

## Files — all forms

| File | What it defines |
|------|-----------------|
| `config.yaml` | The agents (each one a blueprint filled in) and the coordinator's flow |
| `gateway_tools.yaml` | Four tables created for this use case, four blueprint Gateway tools |
| `decisions.yaml` | The decision table — the bank's rules, top to bottom, first match wins |
| `ingestion.yaml` | Banner, footer and customer-number pattern for inbound emails |
| `seed_data.yaml` | Synthetic customers and transactions (no real people) |
| `tools.py` | Two lines binding `decisions.yaml` and `ingestion.yaml` as agent tools |
| `sample_emails/*.json` | Seven scenarios, each with its `_expected_outcome` |

## Scenarios

| Sample | Customer | Expected outcome | Rule |
|--------|----------|------------------|------|
| `01_small_unrecognised_charge` | Mia, $42.50 STREAMFLIX | `provisional_credit` | `small_valid_dispute` |
| `02_large_unrecognised_charge` | Liam, $1,249 TECHWORLD | `human_review` | `valid_dispute_needs_officer` |
| `03_fraud_cluster` | Ava, 3× QUICKBET overnight | `block_card_and_escalate` | `fraud_pattern` |
| `04_forgotten_subscription` | Noah, monthly FITLIFE GYM | `decline_with_explanation` | `likely_genuine` |
| `05_suspended_account` | Grace, suspended account | `human_review` | `account_not_active` |
| `06_unknown_customer` | unknown sender, placeholder number | `request_information` | `unknown_customer` |
| `07_lost_card` | Mia, lost card | `route_to_service_team` | `not_a_dispute` |

The decision rules are unit-tested for every scenario in
`tests/test_decision_table.py`; the agents' judgement is not (it needs a model).

## Run it

```bash
# locally (the Gateway lookups report "unavailable" without a deployment)
sca dev --config app/card_dispute_agent/config.yaml

# deployed: set USE_CASE = "card_dispute_agent" in app/main.py, then
./deploy.sh ap-southeast-2
uv run --project app python scripts/seed_tables.py card_dispute_agent --region ap-southeast-2
python3 scripts/test_invoke.py --region ap-southeast-2 --prompt "$(python3 -c "import json;print(json.load(open('app/card_dispute_agent/sample_emails/01_small_unrecognised_charge.json'))['prompt'])")"
```

Or upload an email to `s3://<inbox bucket>/claims-inbox/card_dispute_agent/` — the
Trigger routes it here and normalises it with `ingestion.yaml`.

## What the framework had to gain for this use case

One generic option, no domain logic: the retrieval blueprint's list mode can now
take its filter value from the caller (`filter.from_input`), with `limit` and
`newest_first` — needed to list *this customer's* transactions. Everything else
the use case needed already existed or is a new blueprint (agent blueprints,
decision tables) that any use case can use.
