"""Supporting Python for the YAML-defined HESTA member-email pipeline.

Nothing here orchestrates anything — ``config.yaml`` does. These modules are the
ported domain logic (``schemas``, ``taxonomy``, ``knowledge``, ``ingestion``),
the deterministic steps exposed as tools (``tools``), and the three pieces of
wiring YAML cannot express on its own (``factory``, ``memory``, ``gateway``).
"""
