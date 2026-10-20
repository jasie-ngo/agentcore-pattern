"""Supporting Python for the YAML-defined insurance claims pipeline.

Nothing here orchestrates anything — ``config.yaml`` does. ``schemas`` holds the
structured-output contracts, ``routing`` the pure routing matrix, ``tools`` the
deterministic Phase 3 executor and ``settings`` the claims-only env vars. The
generic wiring (agent factory, Gateway client, Memory session manager) lives in
``app/_shared/``.
"""
