# Health insurer (illustrative) tool boundary: which tools may the member-enquiry agent call, and with what arguments?
#
# STATUS:      ILLUSTRATIVE — written by the practice for the demo. NOT any real insurer's rules, NOT approved.
# APPROVED BY: (none)
# PURPOSE      Show that a different domain runs on the same core with new files only.
#
# HOW THIS FILE IS USED (same as every pack)
#   @policy      Compiled to Cedar by rules/cedar_pack.py. Each Given line is one policy statement.
#   @must        Run through the real in-process Cedar handler. Same input, same answer.
#
# POLICY VOCABULARY (the only statements the compiler accepts)
#   the tool "<name>" is never permitted
#   the tool "<name>" is permitted
#   the tool "<name>" is permitted only when <field> is "<value>" [and <field> is "<value>" ...]
# Anything not mentioned is denied (default deny). Every name used below must be listed in vocabulary.yaml.

Feature: Health insurer member-enquiry tool boundary

  # ---------------------------------------------------------------------------
  Rule: The agent may read a member's cover and waiting periods

    @policy
    Scenario: Policy
      Given the tool "get_member_cover" is permitted
      And the tool "check_waiting_period" is permitted

    @must
    Scenario Outline: Read tools are allowed
      When the agent calls "<tool>"
      Then the call is allowed

      Examples:
        | tool                 |
        | get_member_cover     |
        | check_waiting_period |

  # ---------------------------------------------------------------------------
  Rule: A pre-existing-condition question goes to the fund's doctor, never to the agent

    @policy
    Scenario: Policy
      Given the tool "escalate_to_fund_doctor" is permitted

    @must
    Scenario: The agent may escalate
      When the agent calls "escalate_to_fund_doctor"
      Then the call is allowed

  # ---------------------------------------------------------------------------
  Rule: The agent never approves or denies a claim

    @policy
    Scenario: Policy
      Given the tool "approve_claim" is never permitted
      And the tool "deny_claim" is never permitted

    @must
    Scenario Outline: Adjudication is refused
      When the agent calls "<tool>"
      Then the call is denied

      Examples:
        | tool          |
        | approve_claim |
        | deny_claim    |

  # ---------------------------------------------------------------------------
  Rule: Case notes the agent writes are internal only

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

  # ---------------------------------------------------------------------------
  Rule: Anything the policy does not mention is denied

    @must
    Scenario: A tool that is not mentioned
      When the agent calls "refund_premium"
      Then the call is denied
