"""An ILLUSTRATIVE finance-insurer client package: financial-hardship and claims enquiries.

Not a real insurer's rules. Everything here (the intents, the wording, the thresholds) is made up for a pre-sale demo with synthetic
data, and must be replaced with the insurer's own approved material before any real use.

Nothing here orchestrates anything: ``clients/fininsurer/pipeline/pipeline.yaml`` does. These modules are the domain logic
(``schemas``, ``taxonomy``, ``knowledge``, ``ingestion``), the deterministic steps exposed as tools (``tools``), and the factory
that binds the client-neutral ``core/factory.py`` to this package's schemas (``factory``).
"""
