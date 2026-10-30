# Finance insurer (illustrative) tool boundary: which tools may the hand-off agent call, and with what arguments?
#
# STATUS:      ILLUSTRATIVE, written for a pre-sale demo. NOT any real insurer's rules, NOT approved. Nothing here is enforced
#              until a person approves it.
# APPROVED BY: (none)
# SOURCES:     clients/fininsurer/pipeline/pipeline.yaml   human_handoff prompt: create_claim is called with status
#                                                          "pending_review" and decision "escalated"; nothing is emailed
#              lambdas/schemas/*.json                      the Gateway tool names and arguments (see vocabulary.yaml)
#              Every rule below is an ASSUMPTION about what an insurer would want, tagged @confirm. There is no insurer document.
#
# WHAT THIS FILE CANNOT EXPRESS (so it is NOT enforced here)
#   The compiler accepts three statement forms about which tools may be called. It cannot say what the agents DECIDE or SAY, so
#   these hardship rules live elsewhere: "a person decides every hardship request" is the routing gate (fininsurer/tools.py,
#   route_decision); "never promise relief" is the Writer and Reviewer prompts and the Guardrail's OutcomeCommitments topic. A
#   numeric limit on estimated_amount cannot be written here either (a rule compares exact text); the Gateway's own policy engine
#   holds the amount limit.
#
# HOW THIS FILE IS USED
#   @policy      Compiled to Cedar by rules/cedar_pack.py. Each Given line is one policy statement.
#   @must        Run through the real in-process Cedar handler. Same input, same answer.
#   @confirm     An assumption. The insurer's compliance reviewer confirms it is intended.
#
# POLICY VOCABULARY (the only statements the compiler accepts)
#   the tool "<name>" is never permitted
#   the tool "<name>" is permitted [for the agent "<agent>"]
#   the tool "<name>" is permitted [for the agent "<agent>"] only when <field> is "<value>" [and <field> is "<value>" ...]
# Anything not mentioned is denied (default deny). Every name used below must be listed in vocabulary.yaml.
#
# In an example, 'the agent "human_handoff"' is the hand-off agent. 'the agent' alone is a placeholder for any other agent, which
# the pack does not allow anything.

Feature: Finance insurer hand-off tool boundary

  # ---------------------------------------------------------------------------
  Rule: The hand-off agent records a case only as waiting for a person, never as decided
    # Source: assumption. This is how the pilot's own design uses create_claim; a hardship case must never be recorded as approved.

    @policy @confirm
    Scenario: Policy
      Given the tool "create_claim" is permitted for the agent "human_handoff" only when status is "pending_review" and decision is "escalated"

    @must @confirm
    Scenario Outline: Status and decision
      When the agent "human_handoff" calls "create_claim" with status "<status>" and decision "<decision>"
      Then the call is <outcome>

      Examples:
        | status         | decision  | outcome |
        | pending_review | escalated | allowed |
        | approved       | approved  | denied  |
        | pending_review | approved  | denied  |
        | approved       | escalated | denied  |

    @must @confirm
    Scenario Outline: A hardship case can be recorded for a person and never as approved
      When the agent "human_handoff" calls "create_claim" with status "pending_review" and decision "<decision>" and category "<category>"
      Then the call is <outcome>

      Examples:
        | category                | decision  | outcome |
        | hardship_premium_relief | escalated | allowed |
        | hardship_claim_support  | escalated | allowed |
        | hardship_premium_relief | approved  | denied  |
        | hardship_claim_support  | approved  | denied  |

    @must @confirm
    Scenario: No other agent may record a case
      When the agent calls "create_claim" with status "pending_review" and decision "escalated"
      Then the call is denied

  # ---------------------------------------------------------------------------
  Rule: The hand-off agent may put a case in front of a person
    # Source: assumption. The hand-off is a record plus a human review request.

    @policy @confirm
    Scenario: Policy
      Given the tool "request_human_review" is permitted for the agent "human_handoff"

    @must @confirm
    Scenario: The hand-off agent may ask for review
      When the agent "human_handoff" calls "request_human_review"
      Then the call is allowed

    @must @confirm
    Scenario: No other agent may
      When the agent calls "request_human_review"
      Then the call is denied

  # ---------------------------------------------------------------------------
  Rule: No agent approves or denies a case
    # Source: assumption. Approving or denying a claim or a hardship request is a reviewer's decision.

    @policy @confirm
    Scenario: Policy
      Given the tool "resolve_claim" is never permitted

    @must @confirm
    Scenario: Resolving is refused to the hand-off agent
      When the agent "human_handoff" calls "resolve_claim"
      Then the call is denied

    @must @confirm
    Scenario: Resolving is refused to any other agent too
      When the agent calls "resolve_claim"
      Then the call is denied

  # ---------------------------------------------------------------------------
  Rule: No agent emails a customer
    # Source: assumption. Every reply is drafted for a person to review and send.

    @policy @confirm
    Scenario: Policy
      Given the tool "send_notification" is never permitted

    @must @confirm
    Scenario: Sending is refused to the hand-off agent
      When the agent "human_handoff" calls "send_notification"
      Then the call is denied

    @must @confirm
    Scenario: Sending is refused to any other agent too
      When the agent calls "send_notification"
      Then the call is denied

  # ---------------------------------------------------------------------------
  Rule: The hand-off agent records, it does not read
    # Source: assumption. Reading the policy and the case list belongs to the agents that do the identity check and the status
    # check. Those agents do not carry this pack today, so this is enforced for the hand-off agent only.

    @must @confirm
    Scenario Outline: Reads are refused to the hand-off agent
      When the agent "human_handoff" calls "<tool>"
      Then the call is denied

      Examples:
        | tool                |
        | lookup_policy       |
        | list_pending_claims |

  # ---------------------------------------------------------------------------
  Rule: Anything the policy does not mention is denied

    @must
    Scenario: A listed tool that no rule permits is denied to any other agent
      When the agent calls "list_pending_claims"
      Then the call is denied
