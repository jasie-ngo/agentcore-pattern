"""The YAML-defined HESTA member-email pipeline.

There is no domain Python here: ``config.yaml`` holds the agents (each one an
agent blueprint filled in) and the flow, ``decisions.yaml`` the deterministic
rules, ``ingestion.yaml`` how inbound messages are cleaned up and
``gateway_tools.yaml`` the Gateway tools. ``tools`` only binds the YAML forms as
agent tools. The generic wiring lives in ``app/_shared/``.
"""
