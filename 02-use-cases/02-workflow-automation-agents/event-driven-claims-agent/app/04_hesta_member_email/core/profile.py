"""The deployment profile: strict (the default) or presale.  See ADR-0015.

* ``strict`` is for any real member data.  It is what runs unless someone says otherwise.
* ``presale`` is an explicit switch for demos with synthetic or de-identified data only.  It relaxes a few production gates
  (for example, a sender-address match counts as verified) and must be visible: every staff report is stamped.

The profile is read from the ``DEPLOYMENT_PROFILE`` environment variable on every call.  Anything that is not exactly
``presale`` (a typo, a different case is fine, an empty value) means strict: a mistake can only make the system stricter,
never looser.  Nothing here can tell whether data is real, so loading real member data into a pre-sale deployment is
prohibited by the decision record, not prevented by code.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

STRICT = "strict"
PRESALE = "presale"
STAMP = "PRE-SALE DEMO: synthetic data only, not for real members"


def current() -> str:
    """The active profile: ``presale`` only when explicitly and exactly asked for, otherwise ``strict``."""
    value = os.getenv("DEPLOYMENT_PROFILE", STRICT).strip().lower()
    if value == PRESALE:
        return PRESALE
    if value not in (STRICT, ""):
        log.warning("DEPLOYMENT_PROFILE=%r is not 'strict' or 'presale'; using strict", value)
    return STRICT


def is_presale() -> bool:
    return current() == PRESALE
