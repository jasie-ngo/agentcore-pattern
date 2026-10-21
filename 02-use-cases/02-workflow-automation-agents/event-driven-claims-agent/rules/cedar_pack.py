"""Compile a tool-boundary rule pack to a Cedar policy, and load it fail closed.

A pack is a directory.  Nothing about any one client is in this file: the client's words are in the pack.

    <pack>/
        <name>.feature         the rules, in a controlled vocabulary (exactly one)
        vocabulary.yaml        the words the rules may use: tools, their arguments, allowed values
        manifest.json          GENERATED: hashes, effective dates, approvals
        compiled/policy.cedar  GENERATED

Commands (an interpreter with `strands-agents[cedar]` and PyYAML installed):

    python3 rules/cedar_pack.py compile PACK   # feature + vocabulary -> compiled/policy.cedar + manifest.json
    python3 rules/cedar_pack.py check PACK     # @must scenarios through the real handler, plus the fail-closed tests
    python3 rules/cedar_pack.py load PACK      # load the pack as it is (refused until it is approved)

The chain this proves, end to end:

    feature file + vocabulary --(deterministic compiler, no LLM)--> policy.cedar
        --(fail-closed loader: hashes, approval, effective dates)--> Strands CedarAuthorization(on_error="deny")

What the loader checks before it builds the handler, and refuses on any failure:
  1. the feature file and the vocabulary still hash to what the manifest says they were compiled from;
  2. the compiled policy hashes to what the manifest says;
  3. recompiling the feature file with the vocabulary gives exactly the policy on disk (so editing both the policy
     and its recorded hash is still caught);
  4. an approval record exists for THIS version of BOTH files (the vocabulary decides what the rules mean);
  5. today falls inside the pack's effective dates.

The handler it returns is built from the verified policy text, not the file path, so the file cannot change between
verification and use.  The approval records written by `check` are TEST FIXTURES in a temporary directory.  Nothing
here approves a real pack: that has to be a person.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_feature import parse_feature  # noqa: E402  - stdlib-only parser for the same Gherkin subset

POLICY_FILE = "compiled/policy.cedar"
VOCABULARY_FILE = "vocabulary.yaml"


class CompileError(Exception):
    """The feature file or the vocabulary cannot be compiled."""


class PackRejected(Exception):
    """The loader refused the pack; the message says why."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Vocabulary ----------------------------------------------------------------------------------------------

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ACTION = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_ .-]*$")  # nothing that could break out of a Cedar string


@dataclass(frozen=True)
class Arg:
    description: str = ""
    values: tuple[str, ...] | None = None  # the only values a rule may compare this argument to, when listed


@dataclass(frozen=True)
class Tool:
    action: str  # the tool's name as the agents see it (the Cedar action)
    description: str
    args: dict[str, Arg]


@dataclass(frozen=True)
class Vocabulary:
    pack_id: str
    jurisdiction: str
    tools: dict[str, Tool]


def _check_keys(where: str, mapping: object, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(mapping, dict):
        raise CompileError(f"vocabulary: {where} must be a mapping")
    extra = set(mapping) - allowed
    missing = required - set(mapping)
    if extra:
        raise CompileError(f"vocabulary: {where} has unknown key(s) {sorted(extra)}; allowed: {sorted(allowed)}")
    if missing:
        raise CompileError(f"vocabulary: {where} is missing {sorted(missing)}")
    return mapping


def parse_vocabulary(text: str) -> Vocabulary:
    """Read and validate a vocabulary. Unknown keys are errors: a typo must not pass silently."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CompileError(f"vocabulary is not valid YAML: {exc}") from exc
    raw = _check_keys("the file", raw, {"pack_id", "jurisdiction", "tools"}, {"pack_id", "jurisdiction", "tools"})
    for key in ("pack_id", "jurisdiction"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise CompileError(f"vocabulary: {key} must be a non-empty string")

    tools: dict[str, Tool] = {}
    for name, body in (raw["tools"] or {}).items():
        if not _NAME.match(str(name)):
            raise CompileError(f"vocabulary: tool name {name!r} must be letters, digits and underscores")
        body = _check_keys(f"tool {name!r}", body, {"action", "description", "args"}, {"action", "args"})
        if not isinstance(body["action"], str) or not _ACTION.match(body["action"]):
            raise CompileError(f"vocabulary: tool {name!r} has an action with characters the compiler will not emit")
        args: dict[str, Arg] = {}
        for arg_name, arg_body in (body["args"] or {}).items():
            if not _NAME.match(str(arg_name)):
                raise CompileError(f"vocabulary: argument name {arg_name!r} of {name!r} is not a plain identifier")
            arg_body = _check_keys(f"{name}.{arg_name}", arg_body or {}, {"description", "values"})
            values = arg_body.get("values")
            if values is not None:
                if not isinstance(values, list) or not values or not all(isinstance(v, str) and _SAFE_VALUE.match(v) for v in values):
                    raise CompileError(f"vocabulary: {name}.{arg_name} values must be a non-empty list of plain text")
                values = tuple(values)
            args[arg_name] = Arg(str(arg_body.get("description", "")), values)
        tools[name] = Tool(body["action"], str(body.get("description", "")), args)
    if not tools:
        raise CompileError("vocabulary: no tools are defined")
    return Vocabulary(raw["pack_id"].strip(), raw["jurisdiction"].strip(), tools)


# --- Compiler ------------------------------------------------------------------------------------------------

_NEVER = re.compile(r'^the tool "(?P<tool>[^"]+)" is never permitted$')
_PERMIT = re.compile(r'^the tool "(?P<tool>[^"]+)" is permitted$')
_ONLY = re.compile(r'^the tool "(?P<tool>[^"]+)" is permitted only when (?P<conds>.+)$')
_COND = re.compile(r'(?P<field>[A-Za-z_][A-Za-z0-9_]*) is "(?P<value>[^"]*)"')


def _tool(vocab: Vocabulary, name: str) -> Tool:
    if name not in vocab.tools:
        raise CompileError(f"unknown tool {name!r}; the vocabulary has: {', '.join(sorted(vocab.tools))}")
    return vocab.tools[name]


def _statement(text: str, vocab: Vocabulary) -> str:
    if m := _NEVER.match(text):
        return f'forbid (principal, action == Action::"{_tool(vocab, m["tool"]).action}", resource);'
    if m := _PERMIT.match(text):
        return f'permit (principal, action == Action::"{_tool(vocab, m["tool"]).action}", resource);'
    if m := _ONLY.match(text):
        tool = _tool(vocab, m["tool"])
        conds = list(_COND.finditer(m["conds"]))
        if not conds or " and ".join(c.group(0) for c in conds) != m["conds"]:
            raise CompileError(f"cannot read the conditions in: {text!r}")
        parts = []
        for c in conds:
            arg = tool.args.get(c["field"])
            if arg is None:
                known = ", ".join(sorted(tool.args)) or "none"
                raise CompileError(f"tool {m['tool']!r} has no argument {c['field']!r} in the vocabulary (known: {known})")
            if arg.values is not None and c["value"] not in arg.values:
                raise CompileError(
                    f"{m['tool']}.{c['field']} cannot be {c['value']!r}; the vocabulary allows: {', '.join(arg.values)}"
                )
            parts.append(f'context.input has {c["field"]} && context.input.{c["field"]} == "{c["value"]}"')
        return f'permit (principal, action == Action::"{tool.action}", resource)\nwhen {{ {" && ".join(parts)} }};'
    raise CompileError(f"not in the policy vocabulary: {text!r}")


def compile_policy(feature_text: str, vocab: Vocabulary, *, feature_name: str, vocabulary_sha: str) -> str:
    """Compile every @policy scenario's steps to Cedar. Deterministic: the same inputs always give the same policy."""
    statements = [
        _statement(step.text, vocab) for sc in parse_feature(feature_text) if "policy" in sc.tags for step in sc.steps
    ]
    if not statements:
        raise CompileError("the feature file has no @policy statements")
    header = (
        "// GENERATED by rules/cedar_pack.py. Do not edit; change the feature file or the vocabulary and recompile.\n"
        f"// pack: {vocab.pack_id}\n"
        f"// source: {feature_name} sha256:{sha256(feature_text.encode('utf-8'))}\n"
        f"// vocabulary: {VOCABULARY_FILE} sha256:{vocabulary_sha}\n"
    )
    return header + "\n".join(statements) + "\n"


def _find_feature(pack_dir: Path) -> Path:
    found = sorted(pack_dir.glob("*.feature"))
    if len(found) != 1:
        raise CompileError(f"a pack has exactly one .feature file; {pack_dir} has {len(found)}")
    return found[0]


def build(pack_dir: Path) -> dict:
    """Compile the pack's feature file with its vocabulary. Writes compiled/policy.cedar and manifest.json."""
    feature_path = _find_feature(pack_dir)
    vocab_path = pack_dir / VOCABULARY_FILE
    if not vocab_path.is_file():
        raise CompileError(f"{pack_dir} has no {VOCABULARY_FILE}")
    feature_bytes, vocab_bytes = feature_path.read_bytes(), vocab_path.read_bytes()
    vocab = parse_vocabulary(vocab_bytes.decode("utf-8"))
    policy = compile_policy(
        feature_bytes.decode("utf-8"), vocab, feature_name=feature_path.name, vocabulary_sha=sha256(vocab_bytes)
    ).encode("utf-8")
    (pack_dir / "compiled").mkdir(exist_ok=True)
    (pack_dir / POLICY_FILE).write_bytes(policy)

    manifest_path = pack_dir / "manifest.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    source_hash, vocab_hash = sha256(feature_bytes), sha256(vocab_bytes)
    kept = [
        a for a in old.get("approvals", [])
        if a.get("source_sha256") == source_hash and a.get("vocabulary_sha256") == vocab_hash
    ]
    dropped = len(old.get("approvals", [])) - len(kept)
    manifest = {
        "pack_id": vocab.pack_id,
        "version": old.get("version", "0.1.0"),
        "jurisdiction": vocab.jurisdiction,
        "effective_from": old.get("effective_from"),  # unset until a person sets it; the loader refuses without it
        "effective_to": old.get("effective_to"),
        "source": {"file": feature_path.name, "sha256": source_hash},
        "vocabulary": {"file": VOCABULARY_FILE, "sha256": vocab_hash},
        "compiled": {"file": POLICY_FILE, "sha256": sha256(policy)},
        "approvals": kept,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if dropped:
        print(f"note: {dropped} earlier approval(s) no longer match the feature file and vocabulary and were dropped")
    return manifest


# --- Fail-closed loader --------------------------------------------------------------------------------------


def verify_pack(pack_dir: Path, today: date | None = None) -> str:
    """Return the verified Cedar policy text, or raise PackRejected saying why."""
    today = today or date.today()
    try:
        manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
        source = (pack_dir / manifest["source"]["file"]).read_bytes()
        vocab_bytes = (pack_dir / manifest["vocabulary"]["file"]).read_bytes()
        policy = (pack_dir / manifest["compiled"]["file"]).read_bytes()
    except (OSError, ValueError, KeyError) as exc:
        raise PackRejected(f"pack is unreadable or incomplete ({type(exc).__name__}: {exc})") from exc

    if sha256(source) != manifest["source"]["sha256"]:
        raise PackRejected("the feature file has changed since it was compiled")
    if sha256(vocab_bytes) != manifest["vocabulary"]["sha256"]:
        raise PackRejected("the vocabulary has changed since it was compiled")
    if sha256(policy) != manifest["compiled"]["sha256"]:
        raise PackRejected("the compiled policy does not match the hash in the manifest")
    try:
        vocab = parse_vocabulary(vocab_bytes.decode("utf-8"))
        expected = compile_policy(
            source.decode("utf-8"), vocab, feature_name=manifest["source"]["file"], vocabulary_sha=sha256(vocab_bytes)
        ).encode("utf-8")
    except CompileError as exc:
        raise PackRejected(f"the feature file and vocabulary no longer compile: {exc}") from exc
    if expected != policy:
        raise PackRejected("the compiled policy is not what the compiler produces from the feature file")

    approved = [
        a for a in manifest.get("approvals", [])
        if a.get("approver") and a.get("source_sha256") == sha256(source) and a.get("vocabulary_sha256") == sha256(vocab_bytes)
    ]
    if not approved:
        raise PackRejected("no approval record for this version of the feature file and vocabulary")

    start, end = manifest.get("effective_from"), manifest.get("effective_to")
    if not start:
        raise PackRejected("effective_from is not set")
    if today < date.fromisoformat(start):
        raise PackRejected(f"not yet effective (effective_from {start})")
    if end and today > date.fromisoformat(end):
        raise PackRejected(f"expired (effective_to {end})")
    return policy.decode("utf-8")


def _principal(invocation_state: dict) -> dict | None:
    pid = (invocation_state or {}).get("principal_id")
    return {"type": "Agent", "id": pid} if pid else None  # None means every call is denied


def load_cedar_authorization(pack_dir: Path, today: date | None = None, principal: dict | None = None):
    """Verify the pack, then build the in-process handler from the verified text. Errors in the handler deny.

    ``principal`` fixes the caller's identity, e.g. ``{"type": "Agent", "id": "human_handoff"}``.  Without it the
    identity is read from ``invocation_state["principal_id"]``, and a call with none is denied.
    """
    from strands.vended_interventions.cedar import CedarAuthorization

    policy = verify_pack(pack_dir, today)
    if principal:
        return CedarAuthorization(policies=policy, principal=principal, on_error="deny")
    return CedarAuthorization(policies=policy, principal_resolver=_principal, on_error="deny")


# --- Checks --------------------------------------------------------------------------------------------------


class _State:
    def __init__(self) -> None:
        self._d: dict = {}

    def get(self, key):
        return self._d.get(key)

    def set(self, key, value) -> None:
        self._d[key] = value


def _event(state: dict, action: str, args: dict):
    """A stand-in for the framework's before-tool-call event. ``action`` is the tool's name at the Gateway."""
    return SimpleNamespace(invocation_state=state, agent=SimpleNamespace(state=_State()),
                           tool_use={"name": action, "input": args, "toolUseId": "t1"})


def _run_must(handler, vocab: Vocabulary, scenario) -> tuple[bool, str]:
    from strands.interventions.actions import Proceed

    state: dict = {"principal_id": "test-agent"}
    call: tuple[str, dict] | None = None
    for step in scenario.steps:
        text = step.text
        if text == "no authenticated principal":
            state = {}
        elif m := re.match(r'^the agent calls "(?P<tool>[^"]+)"(?: with (?P<args>.+))?$', text):
            if m["tool"] not in vocab.tools:
                return False, f"tool {m['tool']!r} is not in the vocabulary"
            args = dict(re.findall(r'(\w+) "([^"]*)"', m["args"] or ""))
            unknown = set(args) - set(vocab.tools[m["tool"]].args)
            if unknown:
                return False, f"{m['tool']} has no argument {sorted(unknown)} in the vocabulary"
            call = (m["tool"], args)
        elif m := re.match(r"^the call is (?P<want>allowed|denied)$", text):
            if call is None:
                return False, "assertion before a call"
            decision = handler.before_tool_call(_event(state, vocab.tools[call[0]].action, call[1]))
            got = "allowed" if isinstance(decision, Proceed) else "denied"
            if got != m["want"]:
                return False, f"expected {m['want']}, got {got}: {getattr(decision, 'reason', '')}"
        else:
            return False, f"step outside the vocabulary: {text!r}"
    return True, ""


def _approve_fixture(pack_dir: Path, **overrides) -> None:
    """TEST FIXTURE ONLY: a stand-in approval inside a temporary directory. It is not an approval of any real pack."""
    path = pack_dir / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["effective_from"] = "2000-01-01"
    manifest["effective_to"] = None
    manifest["approvals"] = [{
        "approver": "TEST-FIXTURE (not a real approval)", "role": "test",
        "source_sha256": manifest["source"]["sha256"], "vocabulary_sha256": manifest["vocabulary"]["sha256"],
        "approved_at": "2000-01-01",
    }]
    manifest.update(overrides)
    path.write_text(json.dumps(manifest, indent=2) + "\n")


def _fresh_pack(tmp: str, name: str, source_pack: Path, **overrides) -> Path:
    """Copy a pack's two source files into a new directory, compile them, and give them a TEST-FIXTURE approval."""
    pack = Path(tmp) / name
    pack.mkdir()
    shutil.copy(_find_feature(source_pack), pack)
    shutil.copy(source_pack / VOCABULARY_FILE, pack)
    build(pack)
    _approve_fixture(pack, **overrides)
    return pack


def _refused(name: str, pack: Path, expect: str, today: date | None = None) -> tuple[bool, str, str]:
    try:
        verify_pack(pack, today)
    except PackRejected as exc:
        ok = expect in str(exc)
        return ok, name, f"refused: {exc}" + ("" if ok else f"   (expected a reason containing {expect!r})")
    return False, name, "LOADED, but the loader should have refused"


def run_checks(source_pack: Path) -> int:
    rows: list[tuple[bool, str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        good = _fresh_pack(tmp, "good", source_pack)
        handler = load_cedar_authorization(good)
        vocab = parse_vocabulary((good / VOCABULARY_FILE).read_text(encoding="utf-8"))
        for sc in parse_feature(_find_feature(good).read_text(encoding="utf-8")):
            if "must" in sc.tags:
                ok, why = _run_must(handler, vocab, sc)
                rows.append((ok, f"scenario: {sc.name}", why))

        # Fail-closed: each of these packs must be refused, for the right reason.
        t = _fresh_pack(tmp, "tamper-policy", source_pack)
        with (t / POLICY_FILE).open("a", encoding="utf-8") as f:
            f.write("permit (principal, action, resource);\n")
        rows.append(_refused("policy edited after compile", t, "does not match the hash"))

        t = _fresh_pack(tmp, "tamper-policy-and-hash", source_pack)
        policy_path = t / POLICY_FILE
        policy_path.write_text(policy_path.read_text() + "permit (principal, action, resource);\n")
        manifest = json.loads((t / "manifest.json").read_text())
        manifest["compiled"]["sha256"] = sha256(policy_path.read_bytes())
        (t / "manifest.json").write_text(json.dumps(manifest))
        rows.append(_refused("policy AND its hash edited together", t, "not what the compiler produces"))

        t = _fresh_pack(tmp, "feature-edited", source_pack)
        with _find_feature(t).open("a", encoding="utf-8") as f:
            f.write("\n# edited after approval\n")
        rows.append(_refused("feature file edited after approval", t, "feature file has changed"))

        t = _fresh_pack(tmp, "vocabulary-edited", source_pack)
        with (t / VOCABULARY_FILE).open("a", encoding="utf-8") as f:
            f.write("\n# edited after approval\n")
        rows.append(_refused("vocabulary edited after approval", t, "vocabulary has changed"))

        t = _fresh_pack(tmp, "stale-approval", source_pack)
        manifest = json.loads((t / "manifest.json").read_text())
        manifest["approvals"][0]["source_sha256"] = "0" * 64
        (t / "manifest.json").write_text(json.dumps(manifest))
        rows.append(_refused("approval is for a different feature file", t, "no approval record"))

        t = _fresh_pack(tmp, "stale-vocabulary-approval", source_pack)
        manifest = json.loads((t / "manifest.json").read_text())
        manifest["approvals"][0]["vocabulary_sha256"] = "0" * 64
        (t / "manifest.json").write_text(json.dumps(manifest))
        rows.append(_refused("approval is for a different vocabulary", t, "no approval record"))

        t = _fresh_pack(tmp, "unapproved", source_pack)
        manifest = json.loads((t / "manifest.json").read_text())
        manifest["approvals"] = []
        (t / "manifest.json").write_text(json.dumps(manifest))
        rows.append(_refused("no approval at all", t, "no approval record"))

        today = date.today()
        rows.append(_refused("not yet effective", _fresh_pack(tmp, "future", source_pack, effective_from=(today + timedelta(days=1)).isoformat()),
                             "not yet effective"))
        rows.append(_refused("expired", _fresh_pack(tmp, "expired", source_pack, effective_to=(today - timedelta(days=1)).isoformat()), "expired"))
        rows.append(_refused("effective_from never set", _fresh_pack(tmp, "no-start", source_pack, effective_from=None), "effective_from is not set"))

        if (source_pack / "manifest.json").exists():
            rows.append(_refused("the pack as it stands (unapproved)", source_pack, "no approval record"))
        else:
            rows.append((False, "the pack as it stands", "run `compile` first: there is no manifest.json"))

    width = max(len(name) for _, name, _ in rows)
    for ok, name, why in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  {why}")
    failed = [r for r in rows if not r[0]]
    print(f"\n{len(rows) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile a rule pack to Cedar and load it fail closed.")
    ap.add_argument("command", choices=["compile", "check", "load"])
    ap.add_argument("pack", type=Path, help="the pack directory (feature file + vocabulary.yaml)")
    args = ap.parse_args()
    pack = args.pack.resolve()

    if args.command == "compile":
        manifest = build(pack)
        print(f"compiled pack {manifest['pack_id']!r} -> {POLICY_FILE}")
        for key in ("source", "vocabulary", "compiled"):
            print(f"  {key:<10} {manifest[key]['file']}  sha256:{manifest[key]['sha256'][:16]}...")
        print(f"  approvals: {len(manifest['approvals'])}   (the loader refuses a pack with none)")
        return 0
    if args.command == "load":
        try:
            load_cedar_authorization(pack)
        except PackRejected as exc:
            print(f"REFUSED: {exc}")
            return 1
        print("loaded: hashes, approval and effective dates all verified")
        return 0
    return run_checks(pack)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CompileError as exc:
        print(f"COMPILE ERROR: {exc}")
        sys.exit(2)
