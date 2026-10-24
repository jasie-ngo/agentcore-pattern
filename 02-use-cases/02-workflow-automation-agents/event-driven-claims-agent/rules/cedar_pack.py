#!/usr/bin/env python3
"""Command line for the rule-pack tooling. The implementation is app/04_hesta_member_email/core/cedar_pack.py.

    python3 rules/cedar_pack.py compile PACK
    python3 rules/cedar_pack.py check PACK
    python3 rules/cedar_pack.py load PACK

It lives in the core package so that it ships inside every client's container. This file only makes it runnable from here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "04_hesta_member_email"))

from core.cedar_pack import CompileError, main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(main())
    except CompileError as exc:
        print(f"COMPILE ERROR: {exc}")
        sys.exit(2)
