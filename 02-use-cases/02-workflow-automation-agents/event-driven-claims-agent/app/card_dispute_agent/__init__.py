"""Card-dispute triage for Harbour Bank (a fictional bank) — built from blueprints.

Every agent is an agent blueprint and every Gateway tool is a tool blueprint,
filled in from this folder's YAML forms:

* ``config.yaml``        — agents (classifier, extractor, assessor, reviewer, writer) and the flow
* ``gateway_tools.yaml`` — tables and Gateway tools (retrieval, record_write)
* ``ingestion.yaml``     — how inbound emails are cleaned up
* ``decisions.yaml``     — the deterministic decision table
* ``seed_data.yaml``     — synthetic customers and transactions for testing

``tools.py`` only binds the decision table and the normaliser. There is no
use-case Python logic at all.
"""
