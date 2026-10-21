"""Display filter for the demo: nothing the audience sees may name a client.

Two different behaviours, on purpose:
  * the client we have worked with is RENAMED on screen (it is real, shown anonymised);
  * a prospect we have not worked with must NEVER appear, so seeing one is an error, not something to paper over.
"""

import re

_RENAMED = re.compile("hesta", re.IGNORECASE)
_NEVER_SHOWN = re.compile("medibank", re.IGNORECASE)


class LeakError(Exception):
    """A name that must never be shown reached the display."""


def _rename(match: re.Match) -> str:
    text, start, end = match.string, match.start(), match.end()
    if match.group(0).isupper():
        return "FUND-A"
    if match.group(0)[0].isupper():
        return "Fund-A"
    # In a slug or identifier keep the separator style: hesta-tool-boundary -> fund-a-tool-boundary.
    near_dash = (start > 0 and text[start - 1] == "-") or (end < len(text) and text[end] == "-")
    return "fund-a" if near_dash else "fund_a"


def clean(text: str) -> str:
    if _NEVER_SHOWN.search(text):
        raise LeakError("a prospect's name reached the display; the demo must not mention it")
    return _RENAMED.sub(_rename, text)
