"""The claim lifecycle: a default shipped in core, extended by a client as data, with safety rules a client cannot weaken.

States and transitions are data (core/lifecycle.default.yaml). ``extend`` validates a client's additions and refuses any that would
let an inbound email skip a person, leave awaiting_staff without a staff event, or strand a state. Nothing here sends anything: the
lifecycle has no "send" event, and every release is a person's. Pure Python plus PyYAML; no I/O beyond reading the default file.

Client-neutral: a client's own states are just names in its overlay.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ESCALATION_STATE = "awaiting_staff"
STAFF_EVENTS = frozenset({"staff_released", "staff_resolved", "staff_closed"})
EVENTS = STAFF_EVENTS | {"inbound_processed", "member_silent"}
_DEFAULT_FILE = Path(__file__).with_name("lifecycle.default.yaml")


class LifecycleError(Exception):
    """The lifecycle is invalid, or no transition applies."""


@dataclass(frozen=True)
class Transition:
    from_state: str
    event: str
    to_state: str
    when: tuple = ()


@dataclass(frozen=True)
class Lifecycle:
    states: tuple
    initial: str
    terminal: tuple
    expects: tuple
    transitions: tuple
    timeouts_days: dict


def _transition(raw: dict) -> Transition:
    when = tuple(sorted((raw.get("when") or {}).items()))
    return Transition(raw["from"], raw["event"], raw["to"], when)


def _build(doc: dict) -> Lifecycle:
    return Lifecycle(
        states=tuple(doc["states"]),
        initial=doc["initial"],
        terminal=tuple(doc.get("terminal", ())),
        expects=tuple(doc.get("expects", ())),
        transitions=tuple(_transition(t) for t in doc["transitions"]),
        timeouts_days=dict(doc.get("timeouts_days", {})),
    )


def default() -> Lifecycle:
    import yaml

    return _build(yaml.safe_load(_DEFAULT_FILE.read_text(encoding="utf-8")))


def _validate(lc: Lifecycle) -> None:
    states = set(lc.states)
    for t in lc.transitions:
        if t.event not in EVENTS:
            raise LifecycleError(f"unknown event {t.event!r}: a client cannot add events")
        for name in (t.from_state, t.to_state):
            if name not in states:
                raise LifecycleError(f"transition names an unknown state {name!r}")
        if t.from_state in lc.terminal:
            raise LifecycleError(f"{t.from_state} is terminal: nothing may leave it")
        for key, value in t.when:
            if key != "expects" or value not in lc.expects:
                raise LifecycleError(f"'when' may only test expects, with a declared value: got {key}={value!r}")
        if t.from_state == ESCALATION_STATE and t.event not in STAFF_EVENTS and t.to_state != ESCALATION_STATE:
            raise LifecycleError(f"only a staff event may leave {ESCALATION_STATE}; {t.event} must stay in it")
        if t.event == "inbound_processed" and t.to_state != ESCALATION_STATE:
            raise LifecycleError(f"an inbound email must always end in {ESCALATION_STATE}")
    for state in lc.states:
        if state in lc.terminal:
            continue
        if not any(t.from_state == state and t.event == "inbound_processed" for t in lc.transitions):
            raise LifecycleError(f"{state} cannot take an inbound_processed event: add a transition to {ESCALATION_STATE}")
    for state, days in lc.timeouts_days.items():
        if state not in states:
            raise LifecycleError(f"timeout for unknown state {state!r}")
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise LifecycleError(f"timeout for {state} must be a whole number of days, at least 1")


def extend(base: Lifecycle, overlay: dict) -> Lifecycle:
    if "initial" in overlay or "terminal" in overlay:
        raise LifecycleError("a client cannot change the initial or terminal states")
    keys = {(t.from_state, t.event, t.when): t for t in base.transitions}
    added = []
    for raw in overlay.get("transitions", []):
        t = _transition(raw)
        existing = keys.get((t.from_state, t.event, t.when))
        if existing is not None:
            if existing.to_state != t.to_state:
                raise LifecycleError(f"cannot redefine default transition {t.from_state} --{t.event}--> {existing.to_state}")
            continue
        added.append(t)
    merged = Lifecycle(
        states=base.states + tuple(s for s in overlay.get("states", []) if s not in base.states),
        initial=base.initial,
        terminal=base.terminal,
        expects=base.expects + tuple(e for e in overlay.get("expects", []) if e not in base.expects),
        transitions=base.transitions + tuple(added),
        timeouts_days={**base.timeouts_days, **overlay.get("timeouts_days", {})},
    )
    _validate(merged)
    return merged


def next_state(lc: Lifecycle, state: str, event: str, flags: dict | None = None) -> str:
    flags = flags or {}
    matches = [
        t for t in lc.transitions
        if t.from_state == state and t.event == event and all(flags.get(k) == v for k, v in t.when)
    ]
    if len(matches) != 1:
        raise LifecycleError(f"no single transition for state={state!r} event={event!r} flags={flags!r} ({len(matches)} match)")
    return matches[0].to_state
