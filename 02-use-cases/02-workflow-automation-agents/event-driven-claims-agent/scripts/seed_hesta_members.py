#!/usr/bin/env python3
"""Seed the existing DynamoDB Policies table with HESTA (superannuation) member records.

This does NOT change the table schema — the partition key stays ``policy_number`` and we
only add item attributes (DynamoDB is schemaless for non-key attributes). The existing
insurance fields are repurposed for super so the agent's AI-003 identity check
(``lookup_policy`` → policy_number / email / policy_type / status) keeps working:

    policy_number   → HESTA member number (the id the agent extracts + looks up)
    holder_name     → member full name           (verification)
    email           → member email               (verification: must match sender)
    policy_type     → super product/account type  (surfaced as account_type)
    status          → active | inactive | closed  (verification: active ⇒ verified)
    coverage_amount → account balance (AUD)        (repurposed; not used for logic)
    deductible      → 0                            (kept for parity; n/a for super)

Extra super attributes (date_of_birth, address, beneficiaries, residency_status, …) are
added per scenario so the email-sample tests are realistic. Each record carries a
``test_scenario`` tag = the mapped agent intent (BDBN/BP/COD/DASP/FH/FLS/NOI/RTC).

Usage:
    python3 scripts/seed_hesta_members.py --list                 # print reference (no AWS)
    python3 scripts/seed_hesta_members.py --dry-run --region ap-southeast-2
    python3 scripts/seed_hesta_members.py --region ap-southeast-2 # write to DynamoDB
"""

from __future__ import annotations

import argparse


def _m(policy_number, holder_name, email, policy_type, balance, status, dob, address, scenario, **extra):
    rec = {
        "policy_number": policy_number,
        "holder_name": holder_name,
        "email": email,
        "policy_type": policy_type,
        "coverage_amount": int(balance),
        "deductible": 0,
        "status": status,
        "date_of_birth": dob,
        "address": address,
        "test_scenario": scenario,
    }
    rec.update(extra)
    return rec


MEMBERS = [
    # ── BDBN — Death Benefit / Binding Death Benefit Nomination ──────────────
    _m("60010001", "Sarah Thompson", "sarah.thompson@example.com", "super_accumulation", 84500, "active",
       "1975-03-12", "12 Wattle St, Brunswick VIC 3056", "BDBN",
       nomination_type="binding", nomination_expiry="2027-06-30",
       beneficiaries=[{"name": "Mark Thompson", "relationship": "spouse", "percentage": 100}]),
    _m("60010002", "James Nguyen", "james.nguyen@example.net", "income_stream", 415000, "active",
       "1958-11-02", "5/40 Beach Rd, Bondi NSW 2026", "BDBN",
       nomination_type="non_binding",
       beneficiaries=[{"name": "Lan Nguyen", "relationship": "spouse", "percentage": 50},
                      {"name": "Kim Nguyen", "relationship": "child", "percentage": 50}]),
    _m("60010003", "Patricia Green", "patricia.green@example.org", "super_accumulation", 156000, "active",
       "1969-07-21", "88 Rundle St, Adelaide SA 5000", "BDBN",
       nomination_type="none", beneficiaries=[]),
    _m("60010004", "David Okafor", "david.okafor@example.com", "super_accumulation", 62300, "active",
       "1982-01-30", "3 Boronia Ave, Logan QLD 4114", "BDBN",
       nomination_type="binding_lapsing", nomination_expiry="2024-05-01",  # expired — needs renewal
       beneficiaries=[{"name": "Ada Okafor", "relationship": "spouse", "percentage": 100}]),
    _m("60010005", "Mei Lin", "mei.lin@example.net", "transition_to_retirement", 298000, "active",
       "1963-09-15", "17 Harbour Esp, Fremantle WA 6160", "BDBN",
       nomination_type="binding", beneficiaries=[{"name": "Estate", "relationship": "legal_personal_rep", "percentage": 100}]),

    # ── BP — Withdrawal / Benefit Payment ────────────────────────────────────
    _m("60020001", "Robert Hayes", "robert.hayes@example.com", "income_stream", 620000, "active",
       "1953-04-18", "9 George St, Hobart TAS 7000", "BP",
       preservation_age_reached=True, condition_of_release="over_65"),
    _m("60020002", "Linda Carmody", "linda.carmody@example.net", "super_accumulation", 210000, "active",
       "1961-02-09", "22 Elgin St, Carlton VIC 3053", "BP",
       preservation_age_reached=True, condition_of_release="retired"),
    _m("60020003", "Anthony Rossi", "anthony.rossi@example.org", "super_accumulation", 45000, "active",
       "1990-08-25", "14 Palm Ct, Cairns QLD 4870", "BP",
       preservation_age_reached=False, condition_of_release="none"),  # not yet eligible
    _m("60020004", "Susan Beckett", "susan.beckett@example.com", "income_stream", 88000, "inactive",
       "1957-12-01", "2/6 Ocean Dr, Glenelg SA 5045", "BP",
       preservation_age_reached=True, condition_of_release="retired"),  # inactive → verification not 'verified'
    _m("60020005", "Grace Mbeki", "grace.mbeki@example.net", "super_accumulation", 133000, "active",
       "1968-06-14", "31 Kent St, Sydney NSW 2000", "BP",
       preservation_age_reached=True, pending_withdrawal=True),  # checking status of existing request

    # ── COD — Change of Personal Details ─────────────────────────────────────
    _m("60030001", "Tom Fletcher", "tom.fletcher@example.com", "super_accumulation", 51000, "active",
       "1988-05-19", "7 Maple St, Ballarat VIC 3350", "COD",
       mol_access=False, mobile_on_file="0400 000 001"),
    _m("60030002", "Aisha Rahman", "aisha.rahman@example.net", "super_accumulation", 76000, "active",
       "1979-10-03", "40 Queen St, Brisbane QLD 4000", "COD",
       accessibility_need="deaf_prefers_email"),
    _m("60030003", "Peter Walsh", "peter.walsh@example.org", "income_stream", 305000, "active",
       "1960-01-27", "12 Vale St, Newcastle NSW 2300", "COD",
       address_outdated=True),
    _m("60030004", "Chloe Martin", "chloe.martin@example.com", "super_accumulation", 39000, "active",
       "1995-03-08", "5 Rose Ln, Subiaco WA 6008", "COD",
       mol_access=False, mobile_update_needed=True),
    _m("60030005", "Hassan Ali", "hassan.ali@example.net", "super_accumulation", 92000, "active",
       "1974-11-22", "18 Park Rd, Darwin NT 0800", "COD",
       email_change_requested=True),

    # ── DASP — Departing Australia Superannuation Payment ────────────────────
    _m("60040001", "Emma Wilson", "emma.wilson@example.co.uk", "super_accumulation", 18500, "active",
       "1994-07-11", "221B Baker St, London, United Kingdom", "DASP",
       residency_status="temporary_resident", country_of_residence="United Kingdom", visa_status="expired_417"),
    _m("60040002", "Kenji Sato", "kenji.sato@example.jp", "super_accumulation", 26700, "active",
       "1992-02-28", "2-1 Chiyoda, Tokyo, Japan", "DASP",
       residency_status="temporary_resident", country_of_residence="Japan", visa_status="departed"),
    _m("60040003", "Maria Gonzalez", "maria.gonzalez@example.es", "super_accumulation", 12300, "active",
       "1996-09-05", "Calle Mayor 3, Madrid, Spain", "DASP",
       residency_status="temporary_resident", country_of_residence="Spain"),
    _m("60040004", "Liam O'Brien", "liam.obrien@example.com", "super_accumulation", 34000, "active",
       "1990-12-19", "14 Queen St, Auckland, New Zealand", "DASP",
       residency_status="temporary_resident", country_of_residence="New Zealand", kiwisaver_transfer_option=True),
    _m("60040005", "Priya Sharma", "priya.sharma@example.in", "super_accumulation", 9800, "closed",
       "1997-04-02", "12 Marine Dr, Mumbai, India", "DASP",
       residency_status="temporary_resident", country_of_residence="India", dasp_paid=True),  # already paid

    # ── FH — Financial Hardship ──────────────────────────────────────────────
    _m("60050001", "Jason Reid", "jason.reid@example.com", "super_accumulation", 15600, "active",
       "1985-06-30", "3 Short St, Elizabeth SA 5112", "FH",
       hardship_eligible=True, last_hardship_payment=None),
    _m("60050002", "Nicole Adams", "nicole.adams@example.net", "super_accumulation", 22000, "active",
       "1983-01-14", "9 Hill St, Frankston VIC 3199", "FH",
       last_hardship_payment={"date": "2025-06-23", "amount": 10000}),  # EOFY-timing scenario
    _m("60050003", "Wayne Djarra", "wayne.djarra@example.org", "super_accumulation", 41000, "active",
       "1962-08-08", "22 River Rd, Katherine NT 0850", "FH",
       indigenous=True, full_time_worker=True),
    _m("60050004", "Fatima Noor", "fatima.noor@example.com", "super_accumulation", 7300, "active",
       "1991-11-27", "1/14 Station St, Footscray VIC 3011", "FH",
       hardship_eligible=True, document_upload_pending=True),
    _m("60050005", "Craig Stewart", "craig.stewart@example.net", "super_accumulation", 5100, "active",
       "1987-03-17", "6 Lachlan Ave, Dubbo NSW 2830", "FH",
       hardship_eligible=True, on_income_support=True),

    # ── FLS — Family Law Split ───────────────────────────────────────────────
    _m("60060001", "Rebecca Coleman", "rebecca.coleman@example.com", "super_accumulation", 268000, "active",
       "1972-05-06", "44 Toorak Rd, South Yarra VIC 3141", "FLS",
       family_law_flag=True, family_law_status="information_requested"),
    _m("60060002", "Mark Ellison", "mark.ellison@example.net", "income_stream", 512000, "active",
       "1965-09-29", "10 Hastings St, Noosa QLD 4567", "FLS",
       family_law_flag=True, family_law_status="split_pending"),
    _m("60060003", "Sophie Delacroix", "sophie.delacroix@example.org", "super_accumulation", 143000, "active",
       "1980-02-17", "27 Norwood Pde, Norwood SA 5067", "FLS",
       family_law_flag=True, court_date="2026-10-15"),
    _m("60060004", "Andrew Blackwood", "andrew.blackwood@example.com", "super_accumulation", 87000, "active",
       "1978-07-24", "5 Marine Pde, Cottesloe WA 6011", "FLS",
       family_law_flag=True, procedural_fairness_stage=True),
    _m("60060005", "Hannah Whitlock", "hannah.whitlock@example.net", "super_accumulation", 199000, "active",
       "1983-12-11", "88 Flinders St, Melbourne VIC 3000", "FLS",
       family_law_flag=True, family_law_status="information_requested"),

    # ── NOI — Notice of Intent to Claim a Tax Deduction ──────────────────────
    _m("60070001", "Daniel Foster", "daniel.foster@example.com", "super_accumulation", 61000, "active",
       "1981-04-09", "12 Ash St, Geelong VIC 3220", "NOI",
       personal_contributions_fy=15000, noi_lodged=False),
    _m("60070002", "Olivia Bennett", "olivia.bennett@example.net", "super_accumulation", 128000, "active",
       "1976-10-21", "3 Bay St, Glenelg SA 5045", "NOI",
       personal_contributions_fy=27500, noi_lodged=False),
    _m("60070003", "Raj Patel", "raj.patel@example.org", "super_accumulation", 94000, "active",
       "1984-01-05", "19 Crown St, Wollongong NSW 2500", "NOI",
       personal_contributions_fy=50000, bring_forward=True, noi_lodged=False),
    _m("60070004", "Karen Mitchell", "karen.mitchell@example.com", "super_accumulation", 73000, "active",
       "1970-06-16", "7 Vista Ct, Toowoomba QLD 4350", "NOI",
       personal_contributions_fy=10000, noi_lodged=True, noi_received_date="2026-05-28"),
    _m("60070005", "Simon Clarke", "simon.clarke@example.net", "super_accumulation", 55000, "active",
       "1989-09-02", "22 Elm St, Launceston TAS 7250", "NOI",
       personal_contributions_fy=8000, noi_submission_issue=True),

    # ── RTC — Rollover / Transfer / Combine Accounts ─────────────────────────
    _m("60080001", "Natalie Ward", "natalie.ward@example.com", "super_accumulation", 47000, "active",
       "1986-03-28", "5 Grove St, Preston VIC 3072", "RTC",
       rollover_in_progress=True, other_funds=[{"fund": "Rest", "member_no": "R-88213"}]),
    _m("60080002", "Bruce Tan", "bruce.tan@example.net", "income_stream", 340000, "active",
       "1952-11-15", "12 Ridge Rd, Eltham VIC 3095", "RTC",
       other_funds=[{"fund": "Rest", "member_no": "R-55190"}]),
    _m("60080003", "Eleanor Page", "eleanor.page@example.org", "super_accumulation", 71000, "active",
       "1979-08-19", "40 Anzac Pde, Kensington NSW 2033", "RTC",
       transfer_status="processing", transfer_submitted="2026-05-02"),
    _m("60080004", "Gary Nguyen", "gary.nguyen@example.com", "super_accumulation", 22000, "active",
       "1993-05-12", "9 Duke St, Fortitude Valley QLD 4006", "RTC",
       consolidation_requested=True,
       other_funds=[{"fund": "AustralianSuper", "member_no": "A-12345"},
                    {"fund": "HostPlus", "member_no": "H-99887"}]),
    _m("60080005", "Isabella Romano", "isabella.romano@example.net", "super_accumulation", 58000, "active",
       "1981-10-07", "3/22 Marine Tce, Geraldton WA 6530", "RTC",
       transfer_status="not_showing", transfer_submitted="2026-04-27"),  # funds not appearing yet
]


def get_table_name(region, stack_name="AgentCore-ClaimsAgent-dev", logical_suffix="Policies"):
    """Discover the deployed Policies table name; fall back to the naming convention."""
    import boto3

    cfn = boto3.client("cloudformation", region_name=region)
    try:
        resp = cfn.list_stack_resources(StackName=stack_name)
        for r in resp.get("StackResourceSummaries", []):
            if r["ResourceType"] == "AWS::DynamoDB::Table" and logical_suffix in r["LogicalResourceId"]:
                return r["PhysicalResourceId"]
    except Exception:
        pass
    dynamodb = boto3.client("dynamodb", region_name=region)
    try:
        for t in dynamodb.list_tables()["TableNames"]:
            if "Policies" in t:
                return t
    except Exception:
        pass
    return "ClaimsAgent-dev-Policies"


def seed(region, dry_run):
    table_name = get_table_name(region)
    print(f"🌱 Seeding {len(MEMBERS)} HESTA member records into: {table_name} ({region})")
    if dry_run:
        print("   (dry-run: no writes)")
        return
    import boto3

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    with table.batch_writer() as batch:
        for m in MEMBERS:
            batch.put_item(Item=m)
    print(f"✅ Wrote {len(MEMBERS)} records ({_counts()})")


def _counts():
    from collections import Counter

    c = Counter(m["test_scenario"] for m in MEMBERS)
    return ", ".join(f"{k}:{v}" for k, v in sorted(c.items()))


def print_reference():
    """Print a markdown reference of all seeded members grouped by intent (no AWS needed)."""
    groups: dict[str, list] = {}
    for m in MEMBERS:
        groups.setdefault(m["test_scenario"], []).append(m)
    names = {
        "BDBN": "Death Benefit / Binding Death Benefit Nomination",
        "BP": "Withdrawal / Benefit Payment",
        "COD": "Change of Personal Details",
        "DASP": "Departing Australia Superannuation Payment",
        "FH": "Financial Hardship",
        "FLS": "Family Law Split",
        "NOI": "Notice of Intent to Claim a Tax Deduction",
        "RTC": "Rollover / Transfer / Combine Accounts",
    }
    print("# HESTA test members (DynamoDB seed reference)\n")
    print("Reuses the existing `Policies` table (PK `policy_number` = member number). "
          "Send test emails **from the member's email** and reference the **member number** "
          "(e.g. \"member number 60010001\") so AI-003 verifies them.\n")
    for code in ["BDBN", "BP", "COD", "DASP", "FH", "FLS", "NOI", "RTC"]:
        print(f"\n## {code} — {names[code]}\n")
        print("| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |")
        print("|---|---|---|---|---|---|---|---|")
        for m in groups[code]:
            skip = {"policy_number", "holder_name", "email", "policy_type", "coverage_amount",
                    "deductible", "status", "date_of_birth", "address", "test_scenario"}
            notes = ", ".join(f"{k}={m[k]}" for k in m if k not in skip) or "—"
            if len(notes) > 90:
                notes = notes[:90] + "…"
            print(f"| {m['policy_number']} | {m['holder_name']} | {m['email']} | {m['policy_type']} | "
                  f"{m['status']} | {m['date_of_birth']} | ${m['coverage_amount']:,} | {notes} |")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed HESTA member records into the Policies table.")
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written, no AWS writes.")
    parser.add_argument("--list", action="store_true", help="Print a markdown reference of all members (no AWS).")
    args = parser.parse_args()

    if args.list:
        print_reference()
    else:
        seed(args.region, args.dry_run)
