"""The curated layer of a client's knowledge: the voice, the footer and per-intent guidance, as reviewed and approved files.

The Writer's wording used to be inline Python in each client package. It is now a folder of files a person reviewed and the client's
owner approved (core/approval.py), baked into the image. ``load`` verifies the approval first, so an edited or unapproved folder stops
the agent starting: it never serves partial or unreviewed wording.

Folder layout:  curated.json  {"voice": {...}, "intents": {id: {"snippet", "sources": [source ids]}}}
                sources.json  {source id: {"title", "uri"}}
                approval.json (written by scripts/knowledge_review.py)

Client-neutral: the client's name is a parameter, and the intent ids are whatever the client's own folder contains.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.approval import verify_approval

VOICE_KEYS = ("greeting", "signoff", "legal_footer", "identity_verification_request", "personal_advice_decline", "style_guide")
FALLBACK_INTENT = "other_unknown"


class KnowledgeError(Exception):
    """The curated folder is missing, malformed, or holds an entry that is not grounded in a cited source."""


@dataclass(frozen=True)
class Knowledge:
    version: str
    voice: dict
    intents: dict
    sources: dict

    def snippet_for(self, intent_id: str) -> str:
        entry = self.intents.get(intent_id) or self.intents[FALLBACK_INTENT]
        return entry["snippet"]


def _read(root: Path, name: str):
    path = Path(root) / name
    if not path.is_file():
        raise KnowledgeError(f"{path} not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise KnowledgeError(f"{path} is not valid JSON: {exc}") from exc


def parse(root: Path, *, version: str = "unapproved") -> Knowledge:
    """Validate a curated folder. Does NOT check approval: use ``load`` at runtime."""
    if not Path(root).is_dir():
        raise KnowledgeError(f"knowledge folder {root} not found")
    curated, sources = _read(root, "curated.json"), _read(root, "sources.json")
    voice, intents = curated.get("voice"), curated.get("intents")
    if not isinstance(voice, dict) or not isinstance(intents, dict) or not isinstance(sources, dict):
        raise KnowledgeError("curated.json needs voice and intents objects, and sources.json an object")
    for key in VOICE_KEYS:
        if not isinstance(voice.get(key), str) or not voice[key].strip():
            raise KnowledgeError(f"voice.{key} is missing or blank")
    if FALLBACK_INTENT not in intents:
        raise KnowledgeError(f"intents must include {FALLBACK_INTENT}")
    for intent_id, entry in intents.items():
        if not isinstance(entry, dict) or not str(entry.get("snippet", "")).strip():
            raise KnowledgeError(f"intent {intent_id} has no snippet")
        cited = entry.get("sources")
        if not isinstance(cited, list) or not cited:
            raise KnowledgeError(f"intent {intent_id} needs at least one source")
        for source_id in cited:
            if source_id not in sources:
                raise KnowledgeError(f"intent {intent_id} cites {source_id}, which is not in sources.json")
    return Knowledge(version=version, voice=voice, intents=intents, sources=sources)


def load(root: Path, *, client: str) -> Knowledge:
    """Verify the approval, then parse. Raises ApprovalError or KnowledgeError: callers must not catch and carry on."""
    record = verify_approval(root, client=client)
    return parse(root, version=record["version"])
