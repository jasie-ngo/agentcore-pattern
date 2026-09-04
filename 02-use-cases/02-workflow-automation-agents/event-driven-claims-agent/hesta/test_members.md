# HESTA test members (DynamoDB seed reference)

Reuses the existing `Policies` table (PK `policy_number` = member number). Send test emails **from the member's email** and reference the **member number** (e.g. "member number 60010001") so AI-003 verifies them.


## BDBN: Death Benefit / Binding Death Benefit Nomination

| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |
|---|---|---|---|---|---|---|---|
| 60010001 | Sarah Thompson | sarah.thompson@example.com | super_accumulation | active | 1975-03-12 | $84,500 | nomination_type=binding, nomination_expiry=2027-06-30, beneficiaries=[{'name': 'Mark Thomp… |
| 60010002 | James Nguyen | james.nguyen@example.net | income_stream | active | 1958-11-02 | $415,000 | nomination_type=non_binding, beneficiaries=[{'name': 'Lan Nguyen', 'relationship': 'spouse… |
| 60010003 | Patricia Green | patricia.green@example.org | super_accumulation | active | 1969-07-21 | $156,000 | nomination_type=none, beneficiaries=[] |
| 60010004 | David Okafor | david.okafor@example.com | super_accumulation | active | 1982-01-30 | $62,300 | nomination_type=binding_lapsing, nomination_expiry=2024-05-01, beneficiaries=[{'name': 'Ad… |
| 60010005 | Mei Lin | mei.lin@example.net | transition_to_retirement | active | 1963-09-15 | $298,000 | nomination_type=binding, beneficiaries=[{'name': 'Estate', 'relationship': 'legal_personal… |

## BP: Withdrawal / Benefit Payment

| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |
|---|---|---|---|---|---|---|---|
| 60020001 | Robert Hayes | robert.hayes@example.com | income_stream | active | 1953-04-18 | $620,000 | preservation_age_reached=True, condition_of_release=over_65 |
| 60020002 | Linda Carmody | linda.carmody@example.net | super_accumulation | active | 1961-02-09 | $210,000 | preservation_age_reached=True, condition_of_release=retired |
| 60020003 | Anthony Rossi | anthony.rossi@example.org | super_accumulation | active | 1990-08-25 | $45,000 | preservation_age_reached=False, condition_of_release=none |
| 60020004 | Susan Beckett | susan.beckett@example.com | income_stream | inactive | 1957-12-01 | $88,000 | preservation_age_reached=True, condition_of_release=retired |
| 60020005 | Grace Mbeki | grace.mbeki@example.net | super_accumulation | active | 1968-06-14 | $133,000 | preservation_age_reached=True, pending_withdrawal=True |

## COD: Change of Personal Details

| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |
|---|---|---|---|---|---|---|---|
| 60030001 | Tom Fletcher | tom.fletcher@example.com | super_accumulation | active | 1988-05-19 | $51,000 | mol_access=False, mobile_on_file=0400 000 001 |
| 60030002 | Aisha Rahman | aisha.rahman@example.net | super_accumulation | active | 1979-10-03 | $76,000 | accessibility_need=deaf_prefers_email |
| 60030003 | Peter Walsh | peter.walsh@example.org | income_stream | active | 1960-01-27 | $305,000 | address_outdated=True |
| 60030004 | Chloe Martin | chloe.martin@example.com | super_accumulation | active | 1995-03-08 | $39,000 | mol_access=False, mobile_update_needed=True |
| 60030005 | Hassan Ali | hassan.ali@example.net | super_accumulation | active | 1974-11-22 | $92,000 | email_change_requested=True |

## DASP: Departing Australia Superannuation Payment

| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |
|---|---|---|---|---|---|---|---|
| 60040001 | Emma Wilson | emma.wilson@example.co.uk | super_accumulation | active | 1994-07-11 | $18,500 | residency_status=temporary_resident, country_of_residence=United Kingdom, visa_status=expi… |
| 60040002 | Kenji Sato | kenji.sato@example.jp | super_accumulation | active | 1992-02-28 | $26,700 | residency_status=temporary_resident, country_of_residence=Japan, visa_status=departed |
| 60040003 | Maria Gonzalez | maria.gonzalez@example.es | super_accumulation | active | 1996-09-05 | $12,300 | residency_status=temporary_resident, country_of_residence=Spain |
| 60040004 | Liam O'Brien | liam.obrien@example.com | super_accumulation | active | 1990-12-19 | $34,000 | residency_status=temporary_resident, country_of_residence=New Zealand, kiwisaver_transfer_… |
| 60040005 | Priya Sharma | priya.sharma@example.in | super_accumulation | closed | 1997-04-02 | $9,800 | residency_status=temporary_resident, country_of_residence=India, dasp_paid=True |

## FH: Financial Hardship

| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |
|---|---|---|---|---|---|---|---|
| 60050001 | Jason Reid | jason.reid@example.com | super_accumulation | active | 1985-06-30 | $15,600 | hardship_eligible=True, last_hardship_payment=None |
| 60050002 | Nicole Adams | nicole.adams@example.net | super_accumulation | active | 1983-01-14 | $22,000 | last_hardship_payment={'date': '2025-06-23', 'amount': 10000} |
| 60050003 | Wayne Djarra | wayne.djarra@example.org | super_accumulation | active | 1962-08-08 | $41,000 | indigenous=True, full_time_worker=True |
| 60050004 | Fatima Noor | fatima.noor@example.com | super_accumulation | active | 1991-11-27 | $7,300 | hardship_eligible=True, document_upload_pending=True |
| 60050005 | Craig Stewart | craig.stewart@example.net | super_accumulation | active | 1987-03-17 | $5,100 | hardship_eligible=True, on_income_support=True |

## FLS: Family Law Split

| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |
|---|---|---|---|---|---|---|---|
| 60060001 | Rebecca Coleman | rebecca.coleman@example.com | super_accumulation | active | 1972-05-06 | $268,000 | family_law_flag=True, family_law_status=information_requested |
| 60060002 | Mark Ellison | mark.ellison@example.net | income_stream | active | 1965-09-29 | $512,000 | family_law_flag=True, family_law_status=split_pending |
| 60060003 | Sophie Delacroix | sophie.delacroix@example.org | super_accumulation | active | 1980-02-17 | $143,000 | family_law_flag=True, court_date=2026-10-15 |
| 60060004 | Andrew Blackwood | andrew.blackwood@example.com | super_accumulation | active | 1978-07-24 | $87,000 | family_law_flag=True, procedural_fairness_stage=True |
| 60060005 | Hannah Whitlock | hannah.whitlock@example.net | super_accumulation | active | 1983-12-11 | $199,000 | family_law_flag=True, family_law_status=information_requested |

## NOI: Notice of Intent to Claim a Tax Deduction

| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |
|---|---|---|---|---|---|---|---|
| 60070001 | Daniel Foster | daniel.foster@example.com | super_accumulation | active | 1981-04-09 | $61,000 | personal_contributions_fy=15000, noi_lodged=False |
| 60070002 | Olivia Bennett | olivia.bennett@example.net | super_accumulation | active | 1976-10-21 | $128,000 | personal_contributions_fy=27500, noi_lodged=False |
| 60070003 | Raj Patel | raj.patel@example.org | super_accumulation | active | 1984-01-05 | $94,000 | personal_contributions_fy=50000, bring_forward=True, noi_lodged=False |
| 60070004 | Karen Mitchell | karen.mitchell@example.com | super_accumulation | active | 1970-06-16 | $73,000 | personal_contributions_fy=10000, noi_lodged=True, noi_received_date=2026-05-28 |
| 60070005 | Simon Clarke | simon.clarke@example.net | super_accumulation | active | 1989-09-02 | $55,000 | personal_contributions_fy=8000, noi_submission_issue=True |

## RTC: Rollover / Transfer / Combine Accounts

| Member # | Name | Email | Product | Status | DOB | Balance | Scenario notes |
|---|---|---|---|---|---|---|---|
| 60080001 | Natalie Ward | natalie.ward@example.com | super_accumulation | active | 1986-03-28 | $47,000 | rollover_in_progress=True, other_funds=[{'fund': 'Rest', 'member_no': 'R-88213'}] |
| 60080002 | Bruce Tan | bruce.tan@example.net | income_stream | active | 1952-11-15 | $340,000 | other_funds=[{'fund': 'Rest', 'member_no': 'R-55190'}] |
| 60080003 | Eleanor Page | eleanor.page@example.org | super_accumulation | active | 1979-08-19 | $71,000 | transfer_status=processing, transfer_submitted=2026-05-02 |
| 60080004 | Gary Nguyen | gary.nguyen@example.com | super_accumulation | active | 1993-05-12 | $22,000 | consolidation_requested=True, other_funds=[{'fund': 'AustralianSuper', 'member_no': 'A-123… |
| 60080005 | Isabella Romano | isabella.romano@example.net | super_accumulation | active | 1981-10-07 | $58,000 | transfer_status=not_showing, transfer_submitted=2026-04-27 |
