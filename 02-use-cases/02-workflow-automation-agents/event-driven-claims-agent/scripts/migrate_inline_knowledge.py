#!/usr/bin/env python3
"""One-off: turn fininsurer's inline wording into its first approved knowledge version. Removed once Task 4 lands.

    ./.venv/bin/python3 scripts/migrate_inline_knowledge.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
sys.path.insert(0, str(APP))

from core import approval  # noqa: E402
from fininsurer import knowledge  # noqa: E402  (the CURRENT, inline version)

VERSION = "20261030-v1"
SRC = "clients/fininsurer/synthetic-sources/"
SOURCES = {
    "hardship-policy": {"title": "Financial Hardship Policy (synthetic)", "uri": SRC + "hardship-policy.html"},
    "claims-guide": {"title": "How to lodge and track a claim (synthetic)", "uri": SRC + "claims-guide.html"},
    "complaints-handling": {"title": "Complaints handling (synthetic)", "uri": SRC + "complaints-handling.md"},
    "general-faq": {"title": "General FAQ (synthetic)", "uri": SRC + "general-faq.txt"},
}
CITES = {
    "hardship_premium_relief": ["hardship-policy"],
    "hardship_claim_support": ["hardship-policy", "claims-guide"],
    "claim_lodgement": ["claims-guide"],
    "claim_status": ["claims-guide"],
    "cancellation_request": ["claims-guide"],
    "complaint_dispute": ["complaints-handling"],
    "change_of_details": ["general-faq"],
    "policy_enquiry": ["general-faq"],
    "other_unknown": ["general-faq"],
}

out = ROOT / "clients" / "fininsurer" / "knowledge" / "approved" / VERSION
out.mkdir(parents=True, exist_ok=True)
curated = {
    "voice": {
        "greeting": knowledge.GREETING,
        "signoff": knowledge.SIGNOFF,
        "legal_footer": knowledge.LEGAL_FOOTER,
        "identity_verification_request": knowledge.IDENTITY_VERIFICATION_REQUEST,
        "personal_advice_decline": knowledge.PERSONAL_ADVICE_DECLINE,
        "style_guide": knowledge.style_guide(),
    },
    "intents": {i: {"snippet": s, "sources": CITES[i]} for i, s in knowledge._SNIPPETS.items()},
}
mismatch = set(curated["intents"]) ^ set(CITES)
assert not mismatch, f"citation map and snippets disagree: {mismatch}"
(out / "curated.json").write_text(json.dumps(curated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
(out / "sources.json").write_text(json.dumps(SOURCES, indent=2) + "\n", encoding="utf-8")
approval.write_approval(
    out, client="fininsurer", version=VERSION, approved_by_name="Synthetic Demo Owner",
    approved_by_role="Synthetic (no real client)", reviewed_by="Synthetic Demo Reviewer", approved_at="2026-10-30T00:00:00Z",
)
(out.parent / "CURRENT").write_text(VERSION + "\n", encoding="utf-8")
print("wrote", out)
