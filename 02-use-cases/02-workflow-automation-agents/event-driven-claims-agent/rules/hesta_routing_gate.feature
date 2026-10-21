# HESTA member-email routing gate: does a human need to review this case?
#
# STATUS:      DRAFT 0.1 — generated from code, NOT yet approved
# APPROVED BY: (pending: compliance / operations SME)
# SOURCES:     app/hesta-claimsagent/routing.py            decide()
#              app/04_hesta_member_email/hesta/tools.py    route_decision(), classify_verification()
#              app/04_hesta_member_email/config.yaml       intent_identifier prompt (personal advice)
#
# TAG LEGEND
#   @must        Enforced by the engine and asserted in tests. Same facts, same answer.
#   @judgement   An upstream model makes the call. Measured against labelled examples (@eval),
#                never asserted case by case.
#   @confirm     Behaviour exists in the code today. SME to confirm it is intended.
#   @drift       Implemented in one code path but not the other. Needs a decision.
#   @adjacent    Related behaviour that is NOT part of the gate itself.
#
# STEP SEMANTICS
#   Every "Given" sets one fact. A later Given for the same fact overrides the Background default.
#   Nothing is ever sent to the member automatically; the draft is shown for staff to review and send.

Feature: HESTA routing gate

  Background:
    Given the confidence threshold is 70
    And the primary intent is "change_of_details"
    And the intent confidence is 85
    And 1 intent scored at or above the threshold
    And no human triage flag is set
    And identity is verified
    And there are no vulnerability flags
    And the priority is "normal"
    And personal advice was not requested
    And no expected attachment is missing

  # ---------------------------------------------------------------------------
  Rule: A clean case needs only a quick staff check

    @must
    Scenario: Nothing triggers escalation
      When the case is routed
      Then no human review is required
      And the draft is shown for staff to review and send

  # ---------------------------------------------------------------------------
  Rule: Regulated requests always go to a human

    @must
    Scenario Outline: Regulated intents escalate
      Given the primary intent is "<intent>"
      When the case is routed
      Then a human must review
      And the reasons include "regulated intent"

      Examples:
        | intent                         |
        | death_benefit_nomination       |
        | withdrawal_benefit_payment     |
        | departing_australia_payment    |
        | financial_hardship             |
        | family_law_split               |
        | notice_of_intent_tax_deduction |

    @must
    Scenario Outline: Non-regulated intents do not escalate on their own
      Given the primary intent is "<intent>"
      When the case is routed
      Then no human review is required

      Examples:
        | intent                    |
        | change_of_details         |
        | rollover_transfer_combine |

  # ---------------------------------------------------------------------------
  Rule: Identity that is not fully verified goes to a human

    @must
    Scenario Outline: Verification level from the member record
      Given the member record is "<record>"
      When identity is assessed
      Then the verification level is "<level>"
      And human verification required is "<required>"

      Examples:
        | record                                      | level      | required |
        | found, active, email matches                | verified   | no       |
        | found, active, email matches ignoring case  | verified   | no       |
        | found, active, email differs                | partial    | yes      |
        | found, closed, email matches                | unverified | yes      |
        | no record found for the number              | unverified | yes      |
        | no member number in the email               | unverified | yes      |
        | lookup service unavailable                  | unverified | yes      |

    @must
    Scenario Outline: A level below verified escalates
      Given identity is not verified with level "<level>"
      When the case is routed
      Then a human must review
      And the reasons include "identity not verified (<level>)"

      Examples:
        | level      |
        | partial    |
        | unverified |

  # ---------------------------------------------------------------------------
  Rule: Unclear or low-confidence intent goes to a human

    @must
    Scenario Outline: Confidence at the threshold is accepted, below it is not
      Given the intent confidence is <confidence>
      When the case is routed
      Then <outcome>

      Examples:
        | confidence | outcome                                                                      |
        | 100        | no human review is required                                                  |
        | 70         | no human review is required                                                  |
        | 69         | a human must review and the reasons include "low intent confidence"          |
        | 40         | a human must review and the reasons include "low intent confidence"          |

    @must
    Scenario: The triage flag escalates regardless of confidence
      Given a human triage flag is set
      When the case is routed
      Then a human must review
      And the reasons include "intent unclear"

    @must @confirm
    Scenario: An unknown intent is reported as unclear, not also as low confidence
      Given the primary intent is "other_unknown"
      And the intent confidence is 0
      When the case is routed
      Then a human must review
      And the reasons include "intent unclear"
      And the reasons do not include "low intent confidence"

  # ---------------------------------------------------------------------------
  Rule: More than one confident intent goes to a human

    @must
    Scenario Outline: Count of intents at or above the threshold
      Given <count> intents scored at or above the threshold
      When the case is routed
      Then <outcome>

      Examples:
        | count | outcome                                                                  |
        | 1     | no human review is required                                              |
        | 2     | a human must review and the reasons include "multiple intents detected"  |
        | 3     | a human must review and the reasons include "multiple intents detected"  |

  # ---------------------------------------------------------------------------
  Rule: Vulnerability or high priority goes to a human

    @must
    Scenario Outline: Any vulnerability flag escalates, even at normal priority
      Given the vulnerability flags are "<flag>"
      When the case is routed
      Then a human must review
      And the reasons include "vulnerability/priority"

      Examples:
        | flag               |
        | financial_distress |
        | bereavement        |
        | accessibility_need |
        | legal_urgency      |
        | elderly            |
        | ill_health         |
        | hardship           |

    @must
    Scenario Outline: Priority on its own
      Given the priority is "<priority>"
      When the case is routed
      Then <outcome>

      Examples:
        | priority | outcome                                                                        |
        | low      | no human review is required                                                    |
        | normal   | no human review is required                                                    |
        | high     | a human must review and the reasons include "vulnerability/priority"           |
        | urgent   | a human must review and the reasons include "vulnerability/priority"           |

  # ---------------------------------------------------------------------------
  Rule: A request for personal financial advice goes to a human

    @must
    Scenario: A flagged request escalates
      Given personal advice was requested
      When the case is routed
      Then a human must review
      And the reasons include "PERSONAL ADVICE requested"

    @must @adjacent
    Scenario: A flagged request is declined, not answered
      Given personal advice was requested
      When the reply is drafted
      Then the reply uses the approved decline wording
      And it offers general information and points to HESTA's advice services

    # The model decides whether a message is a request for personal advice. These examples
    # measure how often it agrees with the SME. They gate a release by agreement rate, not case by case.
    @judgement @eval
    Scenario Outline: Personal advice is recognised
      Given the member writes "<message>"
      Then personal advice requested should be "<label>"

      Examples:
        | message                                          | label |
        | How do I roll over my other super to HESTA?      | no    |
        | What are the eligibility rules?                  | no    |
        | Is it a good idea for me to roll over?           | yes   |
        | Which option or product is best for me?          | yes   |
        | Should I switch to high growth?                  | yes   |
        | What should I do with my super?                  | yes   |

  # ---------------------------------------------------------------------------
  Rule: A missing expected document is raised as a reason

    # DRIFT: hesta-claimsagent/routing.py adds this reason (added in the Phase 1 hardening).
    # 04 route_decision() takes no attachment status, so it cannot. Every intent that expects a
    # document is regulated, so the case still escalates, but this reason is lost.
    @must @drift @confirm
    Scenario: Expected document not detected
      Given the primary intent is "financial_hardship"
      And the expected attachment "bank statement / evidence of hardship" is missing
      When the case is routed
      Then a human must review
      And the reasons include "missing expected attachment (bank statement / evidence of hardship)"

  # ---------------------------------------------------------------------------
  Rule: Reasons accumulate, and any reason escalates

    @must
    Scenario: Several triggers at once
      Given the primary intent is "financial_hardship"
      And identity is not verified with level "unverified"
      And the vulnerability flags are "financial_distress"
      When the case is routed
      Then a human must review
      And the reasons include "regulated intent"
      And the reasons include "identity not verified (unverified)"
      And the reasons include "vulnerability/priority"
      And there are exactly 3 reasons

  # ---------------------------------------------------------------------------
  # Not part of the gate. Included because it sits beside it in the pipeline.
  Rule: A status enquiry gets a real case lookup

    # Today the coordinator prompt guesses this from keywords such as "not showing" and
    # "did you get" (config.yaml:440-444). The trigger is judgement; the consequence is exact.
    @judgement @eval @adjacent
    Scenario: A member is chasing a submission
      Given the member writes "I sent my form last week, did you get it?"
      Then the case is treated as a status enquiry

    @must @adjacent
    Scenario: No case is on file for a status enquiry
      Given the case is a status enquiry
      And no pending case is on file for the member number
      When the reply is drafted
      Then it says the case cannot be located yet
      And it asks the member to confirm how and when they submitted it
      And it states no status, decision or date
