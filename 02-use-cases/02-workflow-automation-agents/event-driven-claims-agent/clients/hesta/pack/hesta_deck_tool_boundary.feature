# HESTA tool boundary, drafted from the "Prioritised AI Agentic Capability" deck (Hesta-POC.pptx)
#
# STATUS:      DRAFT 0.1 — drafted by an assistant from the deck. NOT approved. Nothing here is enforced until it is.
# APPROVED BY: (pending: compliance / operations SME and an engineer)
# SOURCES:     clients/hesta/Hesta-POC.pptx
#                slide 1  the capability table: AI-001, AI-002, AI-003, AI-005, AI-011, AI-012, AI-004, AI-013, AI Dashboard
#                slides 2 and 3  the future-state financial hardship journey (their text is the same; the files differ)
#
# WHAT THE DECK DOES AND DOES NOT SAY
#   The deck names agents and their jobs. It names NO tools, tool arguments or record statuses. So every rule below
#   maps a sentence in the deck to this system's tool names (from vocabulary.yaml) by INFERENCE. Each such mapping is
#   tagged @confirm: the SME confirms the deck's sentence means this, and the engineer confirms the tool is the right one.
#
# WHAT THIS FILE CANNOT EXPRESS (so it is NOT enforced here)
#   The compiler accepts three statement forms about which tools may be called. These deck statements are about what
#   the agents DECIDE or SAY, so they need other kinds of rule (a routing-gate feature file, or plug-in rule kinds):
#     AI-001  a confidence score, detecting several requests in one email, detecting attachments
#     AI-005  vulnerability and complaint indicators, sentiment and priority
#     AI-004  whether a document is complete, valid or missing information
#     AI-012  accuracy, tone, completeness and compliance of a draft
#     AI-013  human corrections becoming structured learning inputs
#     AI Dashboard  reporting, not a tool boundary
#
# HOW THIS FILE IS USED
#   @policy      Compiled to Cedar by rules/cedar_pack.py. Each Given line is one policy statement.
#   @must        Run through the real in-process Cedar handler. Same input, same answer.
#   @confirm     An inference or assumption. The SME confirms it is intended.
#
# POLICY VOCABULARY (the only statements the compiler accepts)
#   the tool "<name>" is never permitted
#   the tool "<name>" is permitted
#   the tool "<name>" is permitted only when <field> is "<value>" [and <field> is "<value>" ...]
# Anything not mentioned is denied (default deny). Every name below must be listed in vocabulary.yaml.

Feature: HESTA tool boundary drafted from the capability deck

  # ---------------------------------------------------------------------------
  Rule: The agents may read the member's record and the case history, and ask for human review
    # Source: slide 1 AI-003 "assembles a trusted member profile by matching available identifiers";
    #         slide 1 AI-002 "reconstructs the conversation" and "history of the case";
    #         slides 2-3 "Has my application progressed?"; "human oversight for regulated decisions".
    # Inference: AI-003 reads through lookup_policy, case history through list_pending_claims, oversight through
    #            request_human_review.

    @policy @confirm
    Scenario: Policy
      Given the tool "lookup_policy" is permitted
      And the tool "list_pending_claims" is permitted
      And the tool "request_human_review" is permitted

    @must @confirm
    Scenario Outline: Read and review tools are allowed
      When the agent calls "<tool>"
      Then the call is allowed

      Examples:
        | tool                 |
        | lookup_policy        |
        | list_pending_claims  |
        | request_human_review |

  # ---------------------------------------------------------------------------
  Rule: A case the agents create is always waiting for a person
    # Source: slide 1 AI-003 "maintaining appropriate human verification before regulated actions are taken";
    #         slides 2-3 "Human specialists remain responsible for regulated assessments and approval decisions".
    # Assumption: the deck does not name a record status. "pending_review" and "escalated" come from the pilot's own
    #             design (config.yaml, human_handoff), not from the deck.

    @policy @confirm
    Scenario: Policy
      Given the tool "create_claim" is permitted only when status is "pending_review" and decision is "escalated"

    @must @confirm
    Scenario Outline: A created case cannot be approved by the agent
      When the agent calls "create_claim" with status "<status>" and decision "<decision>"
      Then the call is <outcome>

      Examples:
        | status         | decision  | outcome |
        | pending_review | escalated | allowed |
        | approved       | approved  | denied  |
        | pending_review | approved  | denied  |
        | approved       | escalated | denied  |

  # ---------------------------------------------------------------------------
  Rule: The agents never close or decide a case
    # Source: slide 1 AI-005 "Human agents remain responsible for all operational decisions";
    #         slides 2-3 "Human specialists remain responsible for regulated assessments and approval decisions".
    # Inference: resolve_claim is the tool that closes a case. It is deliberately NOT named in the policy, so the
    #            default deny applies; the scenario below proves it.

    @must @confirm
    Scenario: Resolving a case is refused
      When the agent calls "resolve_claim"
      Then the call is denied

  # ---------------------------------------------------------------------------
  Rule: Nothing is sent to the member by the agents
    # Source: slide 1 AI-011 "enabling human agents to review rather than create responses from scratch".
    # Assumption (strong): the deck says people REVIEW drafts. It does not say sending is blocked. The "nothing is
    #             sent" rule is the pilot's own design decision (AGENTS.md, pilot reuse decisions). The SME must
    #             confirm this is the intended rule for the early release.

    @policy @confirm
    Scenario: Policy
      Given the tool "send_notification" is never permitted

    @must @confirm
    Scenario: The notification tool is refused
      When the agent calls "send_notification"
      Then the call is denied

  # ---------------------------------------------------------------------------
  Rule: Without a known caller, every call is denied

    @must
    Scenario: No authenticated principal
      Given no authenticated principal
      When the agent calls "lookup_policy"
      Then the call is denied
