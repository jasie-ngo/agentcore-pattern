"""Supporting Python for the YAML-defined HESTA member-email pipeline.

Nothing here orchestrates anything — ``config.yaml`` does. These modules are the
ported domain logic (``schemas``, ``taxonomy``, ``knowledge``, ``ingestion``),
the deterministic steps exposed as tools (``tools``) and the HESTA-only settings.
The generic wiring (agent factory, Gateway client, Memory session manager) lives
in ``app/_shared/``.
"""
