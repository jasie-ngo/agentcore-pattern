"""Use-case-neutral plumbing shared by every agent under ``app/``.

Nothing here knows about a domain.  ``factory`` builds agents (structured output
+ Gateway attachment), ``gateway`` builds the Identity-authenticated MCP client,
``memory`` builds the AgentCore Memory session manager (with a local fallback),
and ``settings`` reads the generic env vars.  Each use case's ``config.yaml``
references these files directly via ``../_shared/<file>.py:<name>``.
"""
