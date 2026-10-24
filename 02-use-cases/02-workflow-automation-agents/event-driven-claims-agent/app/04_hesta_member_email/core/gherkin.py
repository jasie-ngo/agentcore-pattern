"""A small Gherkin parser (stdlib only), shared by the rule-pack compiler and the conformance runner.

Supported: Feature, Background, Rule, Scenario, Scenario Outline + Examples tables, tags, comments. Anything else is an
error, not a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Step:
    keyword: str
    text: str
    line: int


@dataclass
class Scenario:
    name: str
    rule: str
    tags: set[str]
    line: int
    steps: list[Step]


_STEP_RE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$")
_PLACEHOLDER_RE = re.compile(r"<([^<>]+)>")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_feature(text: str) -> list[Scenario]:
    """Parse the Gherkin subset the feature file uses; anything else is an error, not a guess.

    Supported: Feature, Background, Rule, Scenario, Scenario Outline + Examples tables, tags,
    comments.  Outlines are expanded to one Scenario per Examples row, with the Background
    steps prepended to every scenario.
    """
    background: list[Step] = []
    scenarios: list[Scenario] = []
    rule = ""
    tags: set[str] = set()
    block: dict | None = None

    def close() -> None:
        nonlocal block
        if block is None:
            return
        if block["kind"] == "background":
            background[:] = block["steps"]
        elif block["kind"] == "scenario":
            scenarios.append(
                Scenario(block["name"], block["rule"], block["tags"], block["line"], background + block["steps"])
            )
        else:  # outline
            if not block["header"]:
                raise ValueError(f"line {block['line']}: Scenario Outline '{block['name']}' has no Examples")
            for row in block["rows"]:
                example = dict(zip(block["header"], row))

                def fill(m: re.Match[str]) -> str:
                    try:
                        return example[m.group(1)]
                    except KeyError:
                        raise ValueError(
                            f"line {block['line']}: <{m.group(1)}> is not a column of '{block['name']}'"
                        ) from None

                steps = [Step(s.keyword, _PLACEHOLDER_RE.sub(fill, s.text), s.line) for s in block["steps"]]
                label = " / ".join(c[:30] for c in row[:2])
                scenarios.append(
                    Scenario(f"{block['name']} [{label}]", block["rule"], block["tags"], block["line"], background + steps)
                )
        block = None

    def open_block(kind: str, name: str, line_no: int) -> None:
        nonlocal block, tags
        close()
        block = {
            "kind": kind, "name": name, "rule": rule, "tags": tags, "line": line_no,
            "steps": [], "header": None, "rows": [], "in_examples": False,
        }
        tags = set()

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            tags |= set(re.findall(r"@(\w+)", line))
        elif line.startswith("Feature:"):
            close()
        elif line.startswith("Rule:"):
            close()
            rule = line.split(":", 1)[1].strip()
            tags = set()
        elif line.startswith("Background:"):
            open_block("background", "Background", line_no)
        elif line.startswith(("Scenario Outline:", "Scenario Template:")):
            open_block("outline", line.split(":", 1)[1].strip(), line_no)
        elif line.startswith(("Scenario:", "Example:")):
            open_block("scenario", line.split(":", 1)[1].strip(), line_no)
        elif line.startswith("Examples:"):
            if block is None or block["kind"] != "outline":
                raise ValueError(f"line {line_no}: Examples outside a Scenario Outline")
            block["in_examples"] = True
        elif line.startswith("|"):
            if block is None or not block["in_examples"]:
                raise ValueError(f"line {line_no}: data tables are only supported under Examples")
            if block["header"] is None:
                block["header"] = _cells(line)
            else:
                block["rows"].append(_cells(line))
        elif (m := _STEP_RE.match(line)) and block is not None:
            block["steps"].append(Step(m.group(1), m.group(2), line_no))
        else:
            raise ValueError(f"line {line_no}: cannot parse {line!r}")
    close()
    return scenarios
