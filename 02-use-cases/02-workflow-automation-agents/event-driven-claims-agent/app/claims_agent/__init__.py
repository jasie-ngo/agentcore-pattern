"""Supporting Python for the YAML-defined insurance claims pipeline.

Nothing here orchestrates anything — ``config.yaml`` does, with both agents built
from agent blueprints and the routing rule in ``decisions.yaml``. ``tools`` holds
the one hand-written piece: the deterministic Phase 3 executor. The generic
wiring (agent factory, Gateway client, Memory session manager) lives in
``app/_shared/``.
"""
