# HESTA tool boundary: which tools may the agents call, and with what arguments?
#
# STATUS:      DRAFT 0.1 — derived from the pilot's documented design, NOT yet approved
# APPROVED BY: (pending: compliance / operations SME)
# SOURCES:     event-driven-claims-agent/AGENTS.md     "Pilot reuse decisions": nothing is auto-sent; the human
#                                                       hand-off is a DynamoDB record via the existing Gateway tools
#              app/04_hesta_member_email/config.yaml   human_handoff prompt: create_claim is called with
#                                                       status "pending_review" and decision "escalated"
#              app/04_hesta_member_email/hesta/gateway.py   tool names as the agents see them
#
# HOW THIS FILE IS USED
#   @policy      Compiled to Cedar by rules/cedar_pack.py. Each Given line is one policy statement.
#   @must        Run through the real in-process Cedar handler (Strands CedarAuthorization). Same input, same answer.
#   @confirm     Derived from design documents rather than enforced code today. SME to confirm it is intended.
#
# POLICY VOCABULARY (the only statements the compiler accepts)
#   the tool "<name>" is never permitted
#   the tool "<name>" is permitted
#   the tool "<name>" is permitted only when <field> is "<value>" [and <field> is "<value>" ...]
# Anything not mentioned is denied (default deny).
#
# THE WORDS: every tool name, argument and value used below must be listed in vocabulary.yaml (same folder). A
# name that is not listed there is refused when the pack is built. The two files are approved together.

Feature: HESTA tool boundary

  # ---------------------------------------------------------------------------
  Rule: Nothing is sent to the member automatically

    @policy @confirm
    Scenario: Policy
      Given the tool "send_notification" is never permitted

    @must @confirm
    Scenario: The notification tool is refused
      When the agent calls "send_notification"
      Then the call is denied

  # ---------------------------------------------------------------------------
  Rule: The agent only reads records and asks for human review

    @policy
    Scenario: Policy
      Given the tool "lookup_policy" is permitted
      And the tool "list_pending_claims" is permitted
      And the tool "request_human_review" is permitted

    @must
    Scenario Outline: Read and review tools are allowed
      When the agent calls "<tool>"
      Then the call is allowed

      Examples:
        | tool                 |
        | lookup_policy        |
        | list_pending_claims  |
        | request_human_review |

  # ---------------------------------------------------------------------------
  Rule: A case the agent creates is always pending human review

    @policy @confirm
    Scenario: Policy
      Given the tool "create_claim" is permitted only when status is "pending_review" and decision is "escalated"

    @must @confirm
    Scenario Outline: create_claim arguments
      When the agent calls "create_claim" with status "<status>" and decision "<decision>"
      Then the call is <outcome>

      Examples:
        | status         | decision  | outcome |
        | pending_review | escalated | allowed |
        | approved       | approved  | denied  |
        | pending_review | approved  | denied  |
        | approved       | escalated | denied  |

  # ---------------------------------------------------------------------------
  Rule: Anything the policy does not mention is denied

    @must
    Scenario: A tool that is not mentioned
      When the agent calls "resolve_claim"
      Then the call is denied

  # ---------------------------------------------------------------------------
  Rule: Without a known caller, every call is denied

    @must
    Scenario: No authenticated principal
      Given no authenticated principal
      When the agent calls "lookup_policy"
      Then the call is denied
