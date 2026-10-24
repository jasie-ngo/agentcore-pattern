#!/usr/bin/env python3
"""Narrated demo: same core, different rules.  About 11 minutes with pauses, a few seconds with --fast.

Offline: no AWS, no LLM, no network.  Every beat is labelled REAL, MOCKED or SLIDE on screen, and every line passes
through demo/anonymise.py, so the client we have worked with is shown as "fund-a" and a prospect cannot appear.

    python3 demo/run_demo.py                 # presenter mode: waits for Enter between beats
    python3 demo/run_demo.py --fast          # no pauses (also what you get when stdin is not a terminal)
    python3 demo/run_demo.py --beat 3        # one beat only
    python3 demo/run_demo.py --fast --no-color > demo/TRANSCRIPT.txt
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT / "app" / "04_hesta_member_email", HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import anonymise  # noqa: E402
from core import cedar_pack  # noqa: E402

PACKS = ROOT / "rules" / "packs"
SUPER_FUND = PACKS / "hesta-tool-boundary"  # shown on screen as fund-a-tool-boundary
INSURER = PACKS / "health-insurer-illustrative"
CORE_FILES = (ROOT / "app" / "04_hesta_member_email" / "core" / "cedar_pack.py",
              ROOT / "app" / "04_hesta_member_email" / "core" / "gherkin.py")

APPROVAL = "the approval record (a labelled test fixture: no real approver, not signed)"


class Screen:
    """Everything the audience sees goes through here, and through the anonymiser."""

    def __init__(self, fast: bool, color: bool):
        self.fast, self.color = fast, color

    def _style(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def line(self, text: str = "") -> None:
        print(anonymise.clean(text))

    def say(self, text: str) -> None:
        self.line(self._style("1", "  > " + text))

    def beat(self, number: int, title: str, label: str) -> None:
        self.line()
        self.line(self._style("1;36", f"== {number}. {title} =="))
        colour = "32" if label.startswith("REAL") else "33"
        self.line(self._style(colour, f"   [{label}]"))

    def code(self, text: str, indent: str = "    ") -> None:
        for raw in text.rstrip("\n").splitlines():
            self.line(indent + raw)

    def pause(self) -> None:
        if self.fast or not sys.stdin.isatty():
            return
        input(anonymise.clean("   (Enter to continue) "))


# --------------------------------------------------------------------------------------------------------------------
# A real Strands agent, a scripted model and a stand-in tool: the model's choice is fixed, everything else is real.

def _make_model_class():
    from strands.models import Model

    class ScriptedModel(Model):
        """Turn 1 asks for one tool call; turn 2 says it is done.  No network, no LLM."""

        def __init__(self, tool_name: str, args: dict):
            self.tool_name, self.args, self.turn = tool_name, args, 0

        def update_config(self, **kwargs):
            pass

        def get_config(self):
            return {}

        async def structured_output(self, *args, **kwargs):
            raise NotImplementedError

        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.turn += 1
            yield {"messageStart": {"role": "assistant"}}
            if self.turn == 1:
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "t1", "name": self.tool_name}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(self.args)}}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "tool_use"}}
            else:
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": "done"}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "end_turn"}}
            yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}

    return ScriptedModel


def _stand_in_tool(action: str, arg_names: list[str], ran: list):
    """A tool named like the Gateway tool, whose body only records that it ran."""
    from strands import tool

    def stand_in(**kwargs):
        ran.append(True)
        return "ok"

    stand_in.__signature__ = inspect.Signature(
        [inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, default="", annotation=str) for n in arg_names]
    )
    stand_in.__annotations__ = {n: str for n in arg_names}
    stand_in.__doc__ = "Stand-in for a Gateway tool."
    return tool(name=action)(stand_in)


def agent_call(approved_pack: Path, agent_name: str, vocab: cedar_pack.Vocabulary, short_name: str, args: dict) -> tuple[bool, str]:
    """Run one scripted tool call through a real Agent with the pack's Cedar handler. Returns (tool body ran, what the model was told)."""
    from strands import Agent

    action = vocab.tools[short_name].action
    handler = cedar_pack.load_cedar_authorization(approved_pack, principal={"type": "Agent", "id": agent_name})
    ran: list = []
    agent = Agent(
        model=_make_model_class()(action, args),
        tools=[_stand_in_tool(action, list(vocab.tools[short_name].args), ran)],
        interventions=[handler],
        system_prompt="demo",
        callback_handler=None,
    )
    agent("go")
    results = [b["toolResult"] for m in agent.messages for b in m.get("content", []) if "toolResult" in b]
    return bool(ran), results[-1]["content"][0]["text"] if results else ""


def show_calls(s: Screen, approved_pack: Path, agent_name: str, calls: list[tuple[str, dict]]) -> None:
    vocab = cedar_pack.parse_vocabulary((approved_pack / cedar_pack.VOCABULARY_FILE).read_text(encoding="utf-8"))
    for short_name, args in calls:
        ran, told = agent_call(approved_pack, agent_name, vocab, short_name, args)
        shown_args = ", ".join(f'{k}="{v}"' for k, v in args.items()) or "no arguments"
        s.line(f"    call  {short_name}({shown_args})")
        if ran:
            s.line("          -> ALLOWED: the tool ran")
        else:
            s.line("          -> BLOCKED: the tool never ran")
            s.line(f"             the model was told: {told.strip()[:100]}")


def show_load(s: Screen, pack: Path, label: str) -> None:
    try:
        cedar_pack.load_cedar_authorization(pack)
        s.line(f"    {label}: LOADED")
    except cedar_pack.PackRejected as exc:
        s.line(f"    {label}: REFUSED. {exc}")


def policy_without_header(pack: Path) -> str:
    text = (pack / cedar_pack.POLICY_FILE).read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.startswith("//"))


def excerpt(path: Path, needle_lines: tuple[str, ...]) -> str:
    return "\n".join(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if any(n in line for n in needle_lines))


def fingerprint() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in CORE_FILES}


# --------------------------------------------------------------------------------------------------------------------

def beat_refusal(s: Screen) -> None:
    s.beat(1, "The refusal", f"REAL: the checks and the refusals.  MOCKED: {APPROVAL}")
    s.say("A rule pack that nobody approved, or that someone edited afterwards, does not load.")
    show_load(s, INSURER, "the pack as it stands, never approved   ")
    with tempfile.TemporaryDirectory() as tmp:
        approved = cedar_pack._fresh_pack(tmp, "approved", INSURER)
        show_load(s, approved, "the same pack with a (test) approval    ")
        policy = approved / cedar_pack.POLICY_FILE
        policy.write_text(policy.read_text(encoding="utf-8") + "permit (principal, action, resource);\n", encoding="utf-8")
        show_load(s, approved, "one line added: 'permit everything'     ")
    s.pause()


def beat_finding(s: Screen) -> None:
    s.beat(2, "The finding", "REAL: the runner executes the feature file against the actual code of both implementations")
    s.say("An anonymised super-fund client had the same routing decision implemented twice. One feature file, run against both.")
    result = subprocess.run(
        [sys.executable, str(ROOT / "rules" / "run_feature.py"), str(ROOT / "rules" / "hesta_routing_gate.feature"), "--adapter", "all"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    out = result.stdout.splitlines()
    s.code("\n".join(out[:7]))
    in_yaml = False
    for line in out:
        if line.startswith("--- yaml_port"):
            in_yaml = True
            continue
        if line.startswith("Awaiting SME"):
            break
        if in_yaml:
            s.line("    " + line)
    s.say("Two implementations of one rule disagree. The feature file found it; nobody had written the rule down in one place.")
    s.pause()


def beat_pack(s: Screen) -> None:
    s.beat(3, "The pack", f"REAL: compile, Cedar evaluation, the Strands agent loop.  MOCKED: the model (scripted, no LLM), the tools (stand-ins) and {APPROVAL}")
    s.say("Plain-language rules, plus a vocabulary that says what the words mean.")
    s.code(excerpt(SUPER_FUND / "hesta_tool_boundary.feature", ('Given the tool "',)))
    s.say("A deterministic compiler (no LLM) turns them into Cedar.")
    s.code(policy_without_header(SUPER_FUND))
    s.say("Now an agent tries three calls. The model's choices are scripted; the policy check is the real one.")
    with tempfile.TemporaryDirectory() as tmp:
        approved = cedar_pack._fresh_pack(tmp, "approved", SUPER_FUND)
        show_calls(s, approved, "human_handoff", [
            ("create_claim", {"status": "pending_review", "decision": "escalated"}),
            ("create_claim", {"status": "approved", "decision": "approved"}),
            ("send_notification", {"to": "member@example.com"}),
        ])
    s.pause()


def beat_swap(s: Screen) -> None:
    s.beat(4, "The swap: health insurer (illustrative)", f"REAL: the same compiler, loader and handler.  ILLUSTRATIVE: these rules were written by us, not by an insurer.  MOCKED: the model, the tools and {APPROVAL}")
    s.say("A different domain. Same core. We add a vocabulary and a feature file, and nothing else.")
    before = fingerprint()
    s.line("    new files:")
    for path in sorted(p for p in INSURER.rglob("*") if p.is_file()):
        s.line(f"      {path.relative_to(ROOT)}")
    s.code(excerpt(INSURER / "health_insurer_tool_boundary.feature", ('Given the tool "', 'And the tool "')))
    s.code(policy_without_header(INSURER))
    with tempfile.TemporaryDirectory() as tmp:
        approved = cedar_pack._fresh_pack(tmp, "approved", INSURER)
        show_calls(s, approved, "member_enquiry", [
            ("check_waiting_period", {"member_id": "M-1", "treatment_type": "hospital"}),
            ("escalate_to_fund_doctor", {"member_id": "M-1", "question": "Is this condition pre-existing?"}),
            ("approve_claim", {"claim_id": "C-1"}),
            ("add_case_note", {"member_id": "M-1", "visibility": "member"}),
        ])
    checks = subprocess.run(
        [sys.executable, str(ROOT / "rules" / "cedar_pack.py"), "check", str(INSURER)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    s.line(f"    the same tamper and approval checks on the new pack: {checks.stdout.strip().splitlines()[-1]}")
    after = fingerprint()
    s.line("    core files (content fingerprint, sha256 prefix, before -> after loading the new pack):")
    for name in before:
        s.line(f"      {name}  {before[name]} -> {after[name]}  {'unchanged' if before[name] == after[name] else 'CHANGED'}")
    status = subprocess.run(["git", "status", "--short", "-uall", "--", str(INSURER.relative_to(ROOT))], cwd=ROOT, capture_output=True, text=True, check=False)
    s.line("    git status for the new pack (new files only):")
    s.code(status.stdout or "(not a git checkout)", indent="      ")
    s.pause()


def beat_not_done(s: Screen) -> None:
    s.beat(5, "Not done yet", "SLIDE: stated plainly")
    for item in (
        "Approvals are not signed. Today an approval is a JSON record anyone could forge.",
        "Nothing delivers a client's pack to a running container. The loader itself now ships in the app's core package.",
        "Rules can be scoped per agent, but only one agent is wired to a pack: an agent with a pack is refused every tool its vocabulary does not list.",
        "Memory is safe only in the YAML port (verified members only); the deployed app still opens it before checking identity. Dates are checked against today, not the case date.",
        "The routing-gate vocabulary is still specific to the first client. Only the tool boundary is extracted.",
        "The framework leans on an alpha, single-maintainer composition library, unpinned in the repo.",
        "No real second client. The health-insurer rules are illustrative, and no health-data threat model exists.",
        "No measured delivery saving. We have no baseline, so we claim none.",
    ):
        s.line(f"    - {item}")
    s.pause()


def beat_ask(s: Screen, weeks: str, hours: str) -> None:
    s.beat(6, "The ask", "SLIDE")
    s.say(f"A time-boxed experiment: {weeks} weeks, {hours} hours, with two real health-insurance reviewers.")
    s.line("    Pass: no core edit forced by the second domain. Fail: any forced core edit, or effort above the hand-build estimate.")
    s.line("    What we are not asking for: a claim that this makes delivery faster. The experiment measures that.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fast", action="store_true", help="no pauses")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--beat", type=int, choices=range(1, 7), help="run one beat only")
    parser.add_argument("--weeks", default="[weeks: set before presenting]")
    parser.add_argument("--hours", default="[hours: set before presenting]")
    args = parser.parse_args()

    s = Screen(fast=args.fast, color=sys.stdout.isatty() and not args.no_color)
    beats = {
        1: lambda: beat_refusal(s),
        2: lambda: beat_finding(s),
        3: lambda: beat_pack(s),
        4: lambda: beat_swap(s),
        5: lambda: beat_not_done(s),
        6: lambda: beat_ask(s, args.weeks, args.hours),
    }
    s.line("Same core, different rules: a rule-pack framework for regulated agents (offline demo)")
    for number in ([args.beat] if args.beat else sorted(beats)):
        beats[number]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
