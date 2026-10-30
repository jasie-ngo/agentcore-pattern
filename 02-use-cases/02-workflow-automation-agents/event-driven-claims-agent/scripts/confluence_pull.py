#!/usr/bin/env python3
"""Read-only pull of named Confluence Cloud spaces into a sources folder. Run by us, on demand.

    CONFLUENCE_EMAIL=svc@example.com CONFLUENCE_API_TOKEN=... \\
      app/04_hesta_member_email/.venv/bin/python3 scripts/confluence_pull.py https://client.atlassian.net --space KB --space POLICY --dest clients/acme/sources

Only GET requests. Only the spaces named with --space (there is no "everything"). The credential is a read-only service account's API
token, taken from the environment, never from an argument, a file, a log line or an error message. The token belongs in the
AgentCore Identity vault and is exported into this shell by whoever runs the job; it is never committed or baked into an image.

Redirects are never followed, and a next link must stay on the same Confluence site over HTTPS, so the credential cannot be sent
elsewhere. Page bodies are written as untrusted Storage-Format XHTML data: downstream ingestion treats them as DATA, never as
instructions or as trusted markup.

This is Confluence CLOUD. Data Center uses a bearer personal access token and a different base path: confirm the client's site type.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_SPACE = re.compile(r"^[A-Za-z0-9~_-]{1,255}$")
_PAGE_ID = re.compile(r"^[0-9A-Za-z_-]+$")
_MAX_PAGES = 1000
_NEXT_REFUSED = "refusing to follow a next link that leaves the Confluence site"


class ConfluenceError(Exception):
    """A readable reason the pull failed. Never contains the credential."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so a 3xx surfaces as HTTPError instead of re-sending the Authorization header elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _open_no_redirect(request, timeout=30):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "page"


def _next_url(base: str, nxt) -> str:
    """Return the next page's URL only if it is a relative /wiki/ path that stays on this site over HTTPS."""
    if not isinstance(nxt, str) or not nxt.startswith("/wiki/"):
        raise ConfluenceError(_NEXT_REFUSED)
    candidate = f"{base}{nxt}"
    parts = urllib.parse.urlsplit(candidate)
    if parts.scheme != "https" or parts.netloc != urllib.parse.urlsplit(base).netloc or "@" in parts.netloc:
        raise ConfluenceError(_NEXT_REFUSED)
    return candidate


def _storage_value(item: dict) -> str:
    body = item.get("body", {})
    storage = body.get("storage", {}) if isinstance(body, dict) else None
    value = storage.get("value", "") if isinstance(storage, dict) else None
    if not isinstance(value, str):
        raise ConfluenceError("a page has an invalid body")
    return value


def pull_space(
    base_url: str,
    space_key: str,
    dest: Path,
    *,
    email: str,
    token: str,
    opener=_open_no_redirect,
    limit: int = 50,
    max_pages: int = _MAX_PAGES,
) -> list[str]:
    if not base_url.startswith("https://"):
        raise ConfluenceError("the Confluence URL must use HTTPS")
    if not _SPACE.match(space_key):
        raise ConfluenceError(f"{space_key!r} is not a valid space key")
    auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
    base = base_url.rstrip("/")
    query = urllib.parse.urlencode({"spaceKey": space_key, "type": "page", "expand": "body.storage", "limit": limit})
    url = f"{base}/wiki/rest/api/content?{query}"
    Path(dest).mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    requested: set[str] = set()
    while url:
        if url in requested:
            raise ConfluenceError(f"space {space_key} pagination repeated a page; stopping")
        if len(requested) >= max_pages:
            raise ConfluenceError(f"space {space_key} has more than {max_pages} pages; stopping")
        requested.add(url)
        request = urllib.request.Request(url, headers={"Authorization": auth, "Accept": "application/json"}, method="GET")
        try:
            with opener(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfluenceError(f"could not read space {space_key} ({type(exc).__name__})") from None  # never chain the request
        results = payload.get("results", []) if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ConfluenceError(f"space {space_key} returned an unexpected response")
        for item in results:
            if not isinstance(item, dict):
                raise ConfluenceError("a page has a missing or invalid id")
            page_id = str(item.get("id", ""))
            if not _PAGE_ID.match(page_id):
                raise ConfluenceError("a page has a missing or invalid id")
            title = item.get("title", "page")
            if not isinstance(title, str):
                raise ConfluenceError("a page has an invalid title")
            body = _storage_value(item)
            if len(written) >= max_pages:
                raise ConfluenceError(f"space {space_key} has more than {max_pages} pages; stopping")
            name = f"{space_key}-{page_id}-{_slug(title)}.html"
            document = f"<html><head><title>{html.escape(title)}</title></head><body>{body}</body></html>"
            (Path(dest) / name).write_text(document, encoding="utf-8")
            written.append(name)
        links = payload.get("_links") or {}
        nxt = links.get("next") if isinstance(links, dict) else None
        url = _next_url(base, nxt) if nxt else ""
    if not written:
        raise ConfluenceError(f"space {space_key} returned no pages (wrong key, or the account cannot see it)")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("base_url")
    ap.add_argument("--space", action="append", required=True, help="a space key; repeat for more")
    ap.add_argument("--dest", required=True, type=Path)
    args = ap.parse_args(argv)
    email, token = os.environ.get("CONFLUENCE_EMAIL", ""), os.environ.get("CONFLUENCE_API_TOKEN", "")
    if not email or not token:
        print("error: set CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN in the environment", file=sys.stderr)
        return 1
    try:
        for space in args.space:
            names = pull_space(args.base_url, space, args.dest, email=email, token=token)
            print(f"{space}: {len(names)} pages -> {args.dest}")
    except ConfluenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
