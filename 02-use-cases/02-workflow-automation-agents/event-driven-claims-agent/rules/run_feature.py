"""Stage 1 runner: execute a controlled-vocabulary feature file against the routing code.

Reads ``hesta_routing_gate.feature`` and runs every executable scenario against the two
implementations of the HESTA routing gate, WITHOUT changing either of them:

    legacy     app/hesta-claimsagent/routing.py                 decide()
    yaml_port  app/04_hesta_member_email/hesta/tools.py         route_decision(), classify_verification()

The feature file is the specification; the code is the thing under test.  Where the two
implementations disagree, the runner says so, which is how drift between them shows up.

Standard library only for the runner itself.  The adapters import the code under test, so run
it with an interpreter that has that code's dependencies (pydantic; strands-agents):

    python3 rules/run_feature.py                        # both implementations
    python3 rules/run_feature.py --adapter legacy -v    # one implementation, every scenario
    python3 rules/run_feature.py --strict-drift         # fail the run on known drift too

Run it as a script, not inside another test process: both apps have modules with the same
names (``config``, ``models``, ``routing``) and this loads them straight onto ``sys.path``.

Statuses
    PASS   the scenario ran and every assertion held
    FAIL   an assertion did not hold
    DRIFT  a FAIL on a scenario tagged @drift: a known, recorded disagreement
    NA     this implementation cannot evaluate the scenario (reason given)
    SKIP   not executable here: tagged @judgement / @eval / @adjacent
    ERROR  the feature uses a step outside the vocabulary, or the environment disagrees with it

Exit code is 1 on any FAIL or ERROR (and on DRIFT with --strict-drift), else 0.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
LEGACY_APP = ROOT / "app" / "hesta-claimsagent"
YAML_PORT_APP = ROOT / "app" / "04_hesta_member_email"
DEFAULT_FEATURE = Path(__file__).resolve().parent / "hesta_routing_gate.feature"

# Scenarios carrying any of these tags are measured or out of scope, never asserted here.
NOT_EXECUTABLE = {"judgement", "eval", "adjacent"}

PASS, FAIL, DRIFT, NA, SKIP, ERROR = "PASS", "FAIL", "DRIFT", "NA", "SKIP", "ERROR"
STATUS_ORDER = [PASS, FAIL, DRIFT, NA, SKIP, ERROR]


class StepError(Exception):
    """The feature steps outside the controlled vocabulary, or the environment disagrees with it."""


class NotSupported(Exception):
    """The implementation under test cannot evaluate this scenario."""


# --- Gherkin subset ------------------------------------------------------------------------------


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


# --- Facts, outcomes and the implementations under test ------------------------------------------


@dataclass
class Facts:
    """Everything the routing gate looks at, set one fact at a time by the Given steps."""

    threshold: int = 70
    intent: str = "change_of_details"
    confidence: int = 85
    confident_count: int = 1
    triage: bool = False
    id_required: bool = False
    id_level: str = "verified"
    flags: list[str] = field(default_factory=list)
    priority: str = "normal"
    advice: bool = False
    missing_doc: str | None = None
    record: str | None = None


@dataclass
class Outcome:
    escalate: bool
    reasons: list[str]


def _import_from(app_dir: Path, *modules: str) -> list:
    """Import modules that live in ``app_dir``; that directory is on sys.path only for the import."""
    saved = list(sys.path)
    sys.path.insert(0, str(app_dir.resolve()))
    try:
        return [importlib.import_module(m) for m in modules]
    finally:
        sys.path[:] = saved


class LegacyAdapter:
    """app/hesta-claimsagent: routing.decide() over the pydantic models."""

    name = "legacy"
    source = "app/hesta-claimsagent/routing.py  decide()"
    ignores: frozenset[str] = frozenset()

    def __init__(self) -> None:
        config, self._models, self._routing = _import_from(LEGACY_APP, "config", "models", "routing")
        self.threshold = config.INTENT_CONFIDENCE_THRESHOLD

    def route(self, f: Facts) -> Outcome:
        m = self._models
        # decide() counts confident intents from the list itself, so build a list that yields
        # the count the scenario states: the primary plus enough extras at the threshold.
        intents = [m.DetectedIntent(intent_id=f.intent, confidence=f.confidence, rationale="feature", evidence_quote="")]
        primary_confident = 1 if f.confidence >= f.threshold else 0
        for i in range(max(0, f.confident_count - primary_confident)):
            intents.append(
                m.DetectedIntent(intent_id=f"other_intent_{i}", confidence=f.threshold, rationale="feature", evidence_quote="")
            )
        decision = self._routing.decide(
            m.IntentResult(
                intents=intents,
                primary_intent_id=f.intent,
                sender_type="member",
                needs_human_triage=f.triage,
                personal_advice_requested=f.advice,
            ),
            m.MemberProfile(
                member_number="M1", matched=True, verification_level=f.id_level, verification_required=f.id_required
            ),
            m.EmpathyAssessment(
                sentiment="neutral", vulnerability_flags=list(f.flags), priority=f.priority, recommended_attention="n/a"
            ),
            m.AttachmentAssessment(
                status="missing" if f.missing_doc else "ok", expected_document=f.missing_doc or "none"
            ),
        )
        return Outcome(decision.escalate_to_human, list(decision.reasons))

    def verify(self, record: str) -> tuple[str, str]:
        raise NotSupported(
            "the verification policy is embedded in the async profile() and needs a live MCP gateway; "
            "it is not callable on its own"
        )


# The member-record phrases the feature file may use, as arguments to classify_verification().
_BASE_RECORD = {
    "member_number": "10042",
    "sender_email": "member@example.com",
    "record_found": True,
    "record_email": "member@example.com",
    "record_status": "active",
    "record_policy_type": "accumulation",
    "lookup_error": "",
    "gateway_unavailable": False,
}
_RECORDS: dict[str, dict] = {
    "found, active, email matches": {},
    "found, active, email matches ignoring case": {"record_email": "MEMBER@Example.COM"},
    "found, active, email differs": {"record_email": "someone.else@example.com"},
    "found, closed, email matches": {"record_status": "closed"},
    "no record found for the number": {"record_found": False, "record_email": "", "record_status": ""},
    "no member number in the email": {"member_number": ""},
    "lookup service unavailable": {
        "record_found": False, "record_email": "", "record_status": "",
        "lookup_error": "Gateway unavailable", "gateway_unavailable": True,
    },
}


class YamlPortAdapter:
    """app/04_hesta_member_email: the @tool functions the YAML pipeline calls."""

    name = "yaml_port"
    source = "app/04_hesta_member_email/hesta/tools.py  route_decision(), classify_verification()"
    # route_decision() has no input for attachment status, so that fact cannot reach it.
    ignores: frozenset[str] = frozenset({"missing_attachment"})

    def __init__(self) -> None:
        (self._tools,) = _import_from(YAML_PORT_APP, "hesta.tools")
        self.threshold = self._tools.INTENT_CONFIDENCE_THRESHOLD

    def route(self, f: Facts) -> Outcome:
        result = self._tools.route_decision(
            primary_intent_id=f.intent,
            primary_confidence=f.confidence,
            confident_intent_count=f.confident_count,
            needs_human_triage=f.triage,
            personal_advice_requested=f.advice,
            verification_required=f.id_required,
            verification_level=f.id_level,
            vulnerability_flags=list(f.flags),
            priority=f.priority,
        )
        return Outcome(result["escalate_to_human"], list(result["reasons"]))

    def verify(self, record: str) -> tuple[str, str]:
        if record not in _RECORDS:
            raise StepError(f"unknown member record phrase {record!r}; known: {sorted(_RECORDS)}")
        profile = self._tools.classify_verification(**{**_BASE_RECORD, **_RECORDS[record]})
        return profile["verification_level"], "yes" if profile["verification_required"] else "no"


# --- Step vocabulary -----------------------------------------------------------------------------


@dataclass
class World:
    facts: Facts
    adapter: LegacyAdapter | YamlPortAdapter
    outcome: Outcome | None = None
    verification: tuple[str, str] | None = None
    notes: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)


STEPS: list[tuple[re.Pattern[str], Callable[..., None]]] = []


def step(pattern: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    def register(fn: Callable[..., None]) -> Callable[..., None]:
        STEPS.append((re.compile(rf"^{pattern}$"), fn))
        return fn

    return register


def bind(text: str) -> tuple[Callable[..., None], dict[str, str]]:
    for pattern, fn in STEPS:
        if m := pattern.match(text):
            return fn, m.groupdict()
    raise StepError(f"no step definition matches: {text!r}")


def _routed(w: World) -> Outcome:
    if w.outcome is None:
        raise StepError("an assertion about routing came before 'the case is routed'")
    return w.outcome


# Given: each step sets one fact.  A later Given for the same fact overrides the Background.


@step(r'the confidence threshold is (?P<n>\d+)')
def _threshold(w: World, n: str) -> None:
    if w.adapter.threshold != int(n):
        raise StepError(f"{w.adapter.name} is configured with threshold {w.adapter.threshold}; the feature assumes {n}")
    w.facts.threshold = int(n)


@step(r'the primary intent is "(?P<intent>[^"]+)"')
def _intent(w: World, intent: str) -> None:
    w.facts.intent = intent


@step(r'the intent confidence is (?P<n>\d+)')
def _confidence(w: World, n: str) -> None:
    w.facts.confidence = int(n)


@step(r'(?P<n>\d+) intents? scored at or above the threshold')
def _count(w: World, n: str) -> None:
    w.facts.confident_count = int(n)


@step(r'(?P<flag>no|a) human triage flag is set')
def _triage(w: World, flag: str) -> None:
    w.facts.triage = flag == "a"


@step(r'identity is verified')
def _identity_verified(w: World) -> None:
    w.facts.id_required, w.facts.id_level = False, "verified"


@step(r'identity is not verified with level "(?P<level>partial|unverified)"')
def _identity_not_verified(w: World, level: str) -> None:
    w.facts.id_required, w.facts.id_level = True, level


@step(r'there are no vulnerability flags')
def _no_flags(w: World) -> None:
    w.facts.flags = []


@step(r'the vulnerability flags are "(?P<flags>[^"]+)"')
def _flags(w: World, flags: str) -> None:
    w.facts.flags = [f.strip() for f in flags.split(",")]


@step(r'the priority is "(?P<priority>low|normal|high|urgent)"')
def _priority(w: World, priority: str) -> None:
    w.facts.priority = priority


@step(r'personal advice was (?P<neg>not )?requested')
def _advice(w: World, neg: str | None) -> None:
    w.facts.advice = neg is None


@step(r'no expected attachment is missing')
def _attachment_ok(w: World) -> None:
    w.facts.missing_doc = None


@step(r'the expected attachment "(?P<doc>[^"]+)" is missing')
def _attachment_missing(w: World, doc: str) -> None:
    w.facts.missing_doc = doc
    if "missing_attachment" in w.adapter.ignores:
        w.notes.append(f"{w.adapter.name} has no input for attachment status, so this fact never reaches it")


@step(r'the member record is "(?P<record>[^"]+)"')
def _record(w: World, record: str) -> None:
    w.facts.record = record


# When


@step(r'the case is routed')
def _route(w: World) -> None:
    w.outcome = w.adapter.route(w.facts)


@step(r'identity is assessed')
def _assess(w: World) -> None:
    if w.facts.record is None:
        raise StepError("'identity is assessed' needs a 'the member record is ...' step first")
    w.verification = w.adapter.verify(w.facts.record)


# Then


@step(r'a human must review and the reasons include "(?P<reason>.+)"')
def _review_with_reason(w: World, reason: str) -> None:
    _must_review(w)
    _reasons_include(w, reason)


@step(r'a human must review')
def _must_review(w: World) -> None:
    out = _routed(w)
    assert out.escalate, "expected a human to review, but the gate did not escalate"


@step(r'no human review is required')
def _no_review(w: World) -> None:
    out = _routed(w)
    assert not out.escalate, f"expected no escalation, but the gate escalated: {out.reasons}"


@step(r'the reasons include "(?P<reason>.+)"')
def _reasons_include(w: World, reason: str) -> None:
    out = _routed(w)
    assert any(reason in r for r in out.reasons), f"no reason contains {reason!r}; reasons were {out.reasons}"


@step(r'the reasons do not include "(?P<reason>.+)"')
def _reasons_exclude(w: World, reason: str) -> None:
    out = _routed(w)
    assert not any(reason in r for r in out.reasons), f"a reason contains {reason!r}; reasons were {out.reasons}"


@step(r'there are exactly (?P<n>\d+) reasons')
def _reason_count(w: World, n: str) -> None:
    out = _routed(w)
    assert len(out.reasons) == int(n), f"expected {n} reasons, got {len(out.reasons)}: {out.reasons}"


@step(r'the verification level is "(?P<level>\w+)"')
def _level(w: World, level: str) -> None:
    assert w.verification is not None, "identity was not assessed"
    assert w.verification[0] == level, f"expected level {level!r}, got {w.verification[0]!r}"


@step(r'human verification required is "(?P<required>yes|no)"')
def _required(w: World, required: str) -> None:
    assert w.verification is not None, "identity was not assessed"
    assert w.verification[1] == required, f"expected required={required!r}, got {w.verification[1]!r}"


@step(r'the draft is shown for staff to review and send')
def _draft_shown(w: World) -> None:
    # A pipeline invariant, not something the routing gate decides, so nothing is asserted here.
    w.unchecked.append("the draft is shown for staff to review and send")


# --- Running and reporting -----------------------------------------------------------------------


@dataclass
class Result:
    status: str
    detail: str = ""
    unchecked: int = 0


def run_scenario(sc: Scenario, adapter: LegacyAdapter | YamlPortAdapter) -> Result:
    skipped_by = sorted(sc.tags & NOT_EXECUTABLE)
    if skipped_by:
        return Result(SKIP, "tagged @" + " @".join(skipped_by))

    try:
        bound = [(s, *bind(s.text)) for s in sc.steps]  # every step must be in the vocabulary
    except StepError as exc:
        return Result(ERROR, str(exc))

    world = World(facts=Facts(), adapter=adapter)
    for s, fn, groups in bound:
        try:
            fn(world, **groups)
        except NotSupported as exc:
            return Result(NA, str(exc))
        except StepError as exc:
            return Result(ERROR, f"line {s.line}: {exc}")
        except AssertionError as exc:
            note = f"  [{'; '.join(world.notes)}]" if world.notes else ""
            return Result(DRIFT if "drift" in sc.tags else FAIL, f"line {s.line}: {s.keyword} {s.text}\n           {exc}{note}")
        except Exception as exc:  # noqa: BLE001 - report an adapter crash instead of aborting the run
            return Result(ERROR, f"line {s.line}: {type(exc).__name__}: {exc}")
    return Result(PASS, unchecked=len(world.unchecked))


def load_adapters(which: str) -> tuple[list, dict[str, str]]:
    adapters, unavailable = [], {}
    for cls in (LegacyAdapter, YamlPortAdapter):
        if which not in ("all", cls.name):
            continue
        try:
            adapters.append(cls())
        except Exception as exc:  # noqa: BLE001 - e.g. the code under test's dependencies are missing
            unavailable[cls.name] = f"{type(exc).__name__}: {exc}"
    return adapters, unavailable


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a controlled-vocabulary feature file against the routing code.")
    ap.add_argument("feature", nargs="?", type=Path, default=DEFAULT_FEATURE)
    ap.add_argument("--adapter", choices=["all", "legacy", "yaml_port"], default="all")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every scenario, not just the exceptions")
    ap.add_argument("--strict-drift", action="store_true", help="treat known drift as a failure")
    args = ap.parse_args(argv)

    text = args.feature.read_text(encoding="utf-8")
    scenarios = parse_feature(text)
    status_line = re.search(r"^# STATUS:\s*(.+)$", text, re.M)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    print(f"Feature : {args.feature.name}  sha256:{digest}  ({len(scenarios)} scenarios after expanding outlines)")
    print(f"Status  : {status_line.group(1).strip() if status_line else '(no STATUS header)'}")

    adapters, unavailable = load_adapters(args.adapter)
    for name, why in unavailable.items():
        print(f"UNAVAILABLE {name}: {why}")
    if not adapters:
        print("No implementation could be loaded; nothing was checked.")
        return 1

    results: dict[str, list[tuple[Scenario, Result]]] = {}
    for adapter in adapters:
        print(f"Adapter : {adapter.name:<9} {adapter.source}  (threshold {adapter.threshold})")
        results[adapter.name] = [(sc, run_scenario(sc, adapter)) for sc in scenarios]

    print()
    for name, rows in results.items():
        counts = Counter(r.status for _, r in rows)
        print(f"{name:<10} " + "  ".join(f"{s} {counts[s]}" for s in STATUS_ORDER))

    show = STATUS_ORDER if args.verbose else [FAIL, DRIFT, ERROR, NA]
    for name, rows in results.items():
        listed = [(sc, r) for sc, r in rows if r.status in show]
        if not listed:
            continue
        print(f"\n--- {name}")
        for sc, r in listed:
            print(f"  {r.status:<5} {args.feature.name}:{sc.line}  {sc.name}")
            if r.detail and (r.status != SKIP or args.verbose):
                print(f"        {r.detail}")

    if len(results) > 1:
        by_scenario: dict[tuple[str, int, str], dict[str, str]] = {}
        for name, rows in results.items():
            for sc, r in rows:
                by_scenario.setdefault((sc.rule, sc.line, sc.name), {})[name] = r.status
        split = {k: v for k, v in by_scenario.items() if PASS in v.values() and {FAIL, DRIFT} & set(v.values())}
        print("\nImplementations disagree on:" if split else "\nImplementations agree on every scenario both could run.")
        for (rule, line, name), v in split.items():
            print(f"  {args.feature.name}:{line}  {name}\n        " + "  ".join(f"{a}={s}" for a, s in v.items()))

    awaiting = sorted({(sc.line, sc.name.split(" [")[0]) for sc in scenarios if "confirm" in sc.tags})
    if awaiting:
        print(f"\nAwaiting SME confirmation (@confirm): {len(awaiting)}")
        for line, name in awaiting:
            print(f"  {args.feature.name}:{line}  {name}")
    unchecked = sum(r.unchecked for rows in results.values() for _, r in rows)
    if unchecked:
        print(f"Declarative steps that assert nothing at this layer: {unchecked}")

    bad = {FAIL, ERROR} | ({DRIFT} if args.strict_drift else set())
    failed = any(r.status in bad for rows in results.values() for _, r in rows)
    print("\nRESULT: " + ("FAILED" if failed else "OK"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
