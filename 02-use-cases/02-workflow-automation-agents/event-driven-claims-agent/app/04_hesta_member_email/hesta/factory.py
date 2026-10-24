"""HESTA's agent factory, referenced from ``config.yaml`` via ``agents.<name>.type``.

The implementation is the client-neutral ``core/factory.py``.  This file only binds it to HESTA's structured-output classes
(``hesta/schemas.py``), so a second client writes the same few lines against its own schemas.

``agent_kwargs`` the YAML may set: ``structured_output`` (a class name in ``hesta/schemas.py``), ``gateway: true`` (attach the
Identity-managed Gateway client) and ``cedar_pack`` (a rule pack directory; off when empty).  See ``core/factory.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# This file is loaded by path (importlib), not as a package member, so the app root has to be importable before `core.*`
# and `hesta.*` resolve.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strands import Agent

from core.factory import build_agent
from hesta import schemas


def hesta_agent(
    *,
    structured_output: str | None = None,
    gateway: bool = False,
    cedar_pack: str | None = None,
    tools: list | None = None,
    **kwargs,
) -> Agent:
    """Build one HESTA pipeline agent. Arguments are those of ``core.factory.build_agent`` minus ``schemas``."""
    return build_agent(
        schemas=schemas,
        structured_output=structured_output,
        gateway=gateway,
        cedar_pack=cedar_pack,
        tools=tools,
        **kwargs,
    )
