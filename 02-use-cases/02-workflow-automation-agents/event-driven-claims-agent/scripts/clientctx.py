#!/usr/bin/env python3
"""Which deployment is this?  The one place the deploy, teardown, Cognito and test scripts get their names from.  ADR-0015, Q7.

    python3 scripts/clientctx.py --client acme plan          # print every resolved name; touches nothing
    eval "$(python3 scripts/clientctx.py --client acme shell)"   # CTX_* variables for a shell script

Two modes, and the difference is the point (the same rule as agentcore/cdk/lib/naming.ts):
  - no client (the default): the ORIGINAL single-client names, exactly.  The repository root is the project.
  - a client: the project is build/<client>/ (made by scripts/assemble_client.py), and every name is derived from it.

A client's names are read from the assembled project's own agentcore.json, not rebuilt from the slug a second time, so a script
and the CDK app cannot disagree about the stack or the credential.  The project must prove it belongs to the client: it has the
assembler's marker file and a BUILD-INFO.json naming the same client.  That check is what stops `--client a` from tearing down
`build/b`.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from assemble_client import MARKER, AssembleError, parse_slug, project_name  # noqa: E402

ROOT = _HERE.parent
LEGACY_PROJECT = "ClaimsAgent"
LEGACY_CREDENTIAL = "cognito-gateway-m2m"
LEGACY_DOMAIN_PREFIX = "claims-agent"
DEFAULT_TARGET = "dev"
DISCOVERY_SUFFIX = "/.well-known/openid-configuration"


class CtxError(Exception):
    """A readable reason the deployment could not be resolved."""


def _sanitize(name: str) -> str:
    """Mirrors bin/cdk.ts: underscores become hyphens in a stack name."""
    return name.replace("_", "-")


@dataclass(frozen=True)
class Context:
    client: str | None
    project_dir: Path
    target: str
    project: str
    credential: str
    app_dir: str  # relative to project_dir: the runtime's codeLocation

    @property
    def mode(self) -> str:
        return "client" if self.client else "legacy"

    @property
    def prefix(self) -> str:
        """The prefix naming.ts puts on tables and functions."""
        return self.client or LEGACY_PROJECT

    @property
    def stack_name(self) -> str:
        return f"AgentCore-{_sanitize(self.project)}-{_sanitize(self.target)}"

    @property
    def cognito_pool_name(self) -> str:
        return f"{self.project}-UserPool"

    @property
    def cognito_app_client_name(self) -> str:
        return f"{self.project}-M2M"

    @property
    def env_file(self) -> Path:
        return self.project_dir / ".env"

    @property
    def cognito_state_file(self) -> Path:
        return self.project_dir / ".cognito-state.json"

    @property
    def agentcore_json(self) -> Path:
        return self.project_dir / "agentcore" / "agentcore.json"

    def domain_prefix(self, account: str) -> str:
        """Cognito domain prefixes are unique per region across all of AWS, so the account is part of it."""
        if self.client:
            return f"{self.client}-auth-{account}"
        return f"{LEGACY_DOMAIN_PREFIX}-{account}"

    def table(self, kind: str) -> str:
        return f"{self.prefix}-{self.target}-{kind}"

    def bucket(self, account: str, region: str) -> str:
        if self.client:
            return f"{self.client}-inbox-{account}-{region}"
        return f"claims-inbox-{account}-{region}"

    def function(self, kind: str) -> str:
        return f"{self.prefix}-{kind}"

    def checked_credential(self, env_value: str | None) -> str:
        """The credential provider name.  In legacy mode the environment may override it (it always could).  In client mode the
        project's agentcore.json decides, and a different value in the environment is refused instead of silently cross-wired."""
        if not self.client:
            return env_value or self.credential
        if env_value and env_value != self.credential:
            raise CtxError(
                f"AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER is set to \"{env_value}\" but {self.client}'s project uses "
                f"\"{self.credential}\". Unset it: a client's credential name comes from its own agentcore.json."
            )
        return self.credential


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CtxError(f"{path} does not exist.") from exc
    except json.JSONDecodeError as exc:
        raise CtxError(f"{path} is not valid JSON: {exc}") from exc


def resolve(
    client: str | None = None,
    project_dir: str | os.PathLike | None = None,
    target: str | None = None,
    env: dict | None = None,
    root: Path = ROOT,
) -> Context:
    env = os.environ if env is None else env
    raw_client = (client if client is not None else env.get("AGENTCORE_CLIENT", "")).strip()
    target = (target or env.get("AGENTCORE_TARGET") or DEFAULT_TARGET).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", target):
        raise CtxError(f'Invalid target "{target}".')
    given_dir = project_dir or env.get("AGENTCORE_PROJECT_DIR") or None

    if not raw_client:
        pdir = Path(given_dir).resolve() if given_dir else root
        spec_path = pdir / "agentcore" / "agentcore.json"
        spec = _read_json(spec_path) if spec_path.is_file() else {}
        creds = spec.get("credentials") or [{}]
        runtimes = spec.get("runtimes") or [{}]
        return Context(
            client=None,
            project_dir=pdir,
            target=target,
            project=spec.get("name", LEGACY_PROJECT),
            credential=creds[0].get("name", LEGACY_CREDENTIAL),
            app_dir=runtimes[0].get("codeLocation", "app/04_hesta_member_email"),
        )

    try:
        slug = parse_slug(raw_client)
    except AssembleError as exc:
        raise CtxError(str(exc)) from exc
    pdir = Path(given_dir).resolve() if given_dir else (root / "build" / slug).resolve()
    hint = f"Assemble it first: python3 scripts/assemble_client.py {slug} --display-name \"...\" --profile presale"
    if not pdir.is_dir():
        raise CtxError(f"There is no project for client {slug} at {pdir}. {hint}")
    if not (pdir / MARKER).is_file():
        raise CtxError(f"{pdir} was not made by scripts/assemble_client.py (no {MARKER}), so it will not be used. {hint}")
    info = _read_json(pdir / "BUILD-INFO.json")
    if info.get("client") != slug:
        raise CtxError(f'{pdir} belongs to client "{info.get("client")}", not "{slug}". Refusing to act on it.')
    spec = _read_json(pdir / "agentcore" / "agentcore.json")
    if spec.get("name") != project_name(slug):
        raise CtxError(f'{pdir}/agentcore/agentcore.json is named "{spec.get("name")}", expected "{project_name(slug)}" for {slug}.')
    return Context(
        client=slug,
        project_dir=pdir,
        target=target,
        project=spec["name"],
        credential=spec["credentials"][0]["name"],
        app_dir=spec["runtimes"][0]["codeLocation"],
    )


def apply_identity(ctx: Context, discovery_url: str, client_id: str) -> None:
    """Put this client's own Cognito identity into its project, in place of the assembler's placeholders.

    Only for a client: the base project's identity is not rewritten by a script."""
    if not ctx.client:
        raise CtxError("apply-identity is only for a client project. The base project's agentcore.json is edited by hand.")
    if not (discovery_url.startswith("https://cognito-idp.") and discovery_url.endswith(DISCOVERY_SUFFIX)):
        raise CtxError(f"The discovery URL must be a Cognito one ending {DISCOVERY_SUFFIX}: {discovery_url!r}")
    if not re.fullmatch(r"[A-Za-z0-9]{10,64}", client_id):
        raise CtxError("The app client id looks wrong (expected 10 to 64 letters and digits).")
    path = ctx.agentcore_json
    spec = _read_json(path)
    spec["credentials"][0]["discoveryUrl"] = discovery_url
    for gateway in spec.get("agentCoreGateways", []):
        jwt = gateway.get("authorizerConfiguration", {}).get("customJwtAuthorizer")
        if jwt is not None:
            jwt["discoveryUrl"] = discovery_url
            jwt["allowedClients"] = [client_id]
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".agentcore-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


# ─── Which orphaned AgentCore resources are this deployment's? ──────────────────────────────────────────────────────────────


def known_projects(root: Path = ROOT) -> set[str]:
    """Project names of every client this checkout knows about: assembled projects and client folders."""
    names: set[str] = set()
    for base in ("build", "clients"):
        directory = root / base
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            if entry.is_dir() and re.fullmatch(r"[a-z][a-z0-9-]{1,22}[a-z0-9]", entry.name) and "--" not in entry.name:
                names.add(project_name(entry.name))
    return names


def _boundary(name: str, project: str) -> bool:
    """`name` starts with `project` and the next character begins a new word (end, separator, or a capital)."""
    if not name.lower().startswith(project.lower()):
        return False
    rest = name[len(project):]
    return rest == "" or rest[0] in "_-." or rest[0].isupper()


def owns_resource(name: str, ctx: Context, siblings: set[str] | None = None) -> bool:
    """Is this AgentCore resource name part of this deployment?

    Legacy mode keeps the original loose test ("claims" anywhere), because the original deployment's gateway, policy engine and
    memory names are not all prefixed.  A client is strict: the project name as a whole word, and not a longer sibling's name
    (client `acme` must not match `AcmeCorp...` when `acme-corp` is also a client of this checkout)."""
    lower = name.lower()
    if not ctx.client:
        return "claimsagent" in lower or "claims" in lower
    if not _boundary(name, ctx.project):
        return False
    for other in siblings or set():
        if other != ctx.project and len(other) > len(ctx.project) and other.lower().startswith(ctx.project.lower()):
            if _boundary(name, other):
                return False
    return True


# ─── Command line ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def shell_vars(ctx: Context) -> dict[str, str]:
    return {
        "CTX_MODE": ctx.mode,
        "CTX_CLIENT": ctx.client or "",
        "CTX_PROJECT_DIR": str(ctx.project_dir),
        "CTX_PROJECT": ctx.project,
        "CTX_TARGET": ctx.target,
        "CTX_STACK_NAME": ctx.stack_name,
        "CTX_CREDENTIAL": ctx.credential,
        "CTX_APP_DIR": ctx.app_dir,
        "CTX_COGNITO_POOL": ctx.cognito_pool_name,
        "CTX_COGNITO_APP_CLIENT": ctx.cognito_app_client_name,
        "CTX_ENV_FILE": str(ctx.env_file),
        "CTX_COGNITO_STATE": str(ctx.cognito_state_file),
    }


def plan_text(ctx: Context, account: str = "<account>", region: str = "<region>") -> str:
    rows = [
        ("mode", ctx.mode + (f" ({ctx.client})" if ctx.client else " (the original single-client names)")),
        ("project directory", str(ctx.project_dir)),
        ("agentcore project", ctx.project),
        ("target", ctx.target),
        ("CloudFormation stack", ctx.stack_name),
        ("credential provider", ctx.credential),
        ("runtime code", ctx.app_dir),
        ("Cognito user pool", ctx.cognito_pool_name),
        ("Cognito app client", ctx.cognito_app_client_name),
        ("Cognito domain prefix", ctx.domain_prefix(account)),
        ("Policies table", ctx.table("Policies")),
        ("Claims table", ctx.table("Claims")),
        ("inbox bucket", ctx.bucket(account, region)),
        (".env file", str(ctx.env_file)),
        ("Cognito state file", str(ctx.cognito_state_file)),
    ]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"  {label:<{width}}  {value}" for label, value in rows)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The arguments every script shares, so `--client` means the same thing everywhere."""
    parser.add_argument("--client", default=None, help="the client slug (default: $AGENTCORE_CLIENT; none = the original names)")
    parser.add_argument("--project-dir", default=None, help="the assembled project (default: build/<client>)")
    parser.add_argument("--target", default=None, help="deployment target (default: $AGENTCORE_TARGET or dev)")


def context_from_args(args: argparse.Namespace) -> Context:
    return resolve(client=args.client, project_dir=args.project_dir, target=args.target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve the names a deployment script needs.")
    add_arguments(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("shell", help="print export CTX_*=... lines for eval")
    sub.add_parser("json", help="print the resolved names as JSON")
    sub.add_parser("plan", help="print the resolved names for a person to read")
    get = sub.add_parser("get", help="print one value")
    get.add_argument("key", choices=["domain-prefix", "table", "bucket", "function", "credential"])
    get.add_argument("--account", default="")
    get.add_argument("--region", default="")
    get.add_argument("--kind", default="")
    ident = sub.add_parser("apply-identity", help="write this client's Cognito identity into its project")
    ident.add_argument("--discovery-url", required=True)
    ident.add_argument("--app-client-id", required=True)
    owns = sub.add_parser("owns", help="exit 0 if the resource name is this deployment's")
    owns.add_argument("name")
    args = parser.parse_args(argv)
    try:
        ctx = context_from_args(args)
        if args.command == "shell":
            for key, value in shell_vars(ctx).items():
                print(f"export {key}={shlex.quote(value)}")
        elif args.command == "json":
            print(json.dumps(shell_vars(ctx), indent=2))
        elif args.command == "plan":
            print(plan_text(ctx))
        elif args.command == "get":
            if args.key == "domain-prefix":
                print(ctx.domain_prefix(args.account))
            elif args.key == "table":
                print(ctx.table(args.kind))
            elif args.key == "bucket":
                print(ctx.bucket(args.account, args.region))
            elif args.key == "function":
                print(ctx.function(args.kind))
            elif args.key == "credential":
                print(ctx.checked_credential(os.environ.get("AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER")))
        elif args.command == "apply-identity":
            apply_identity(ctx, args.discovery_url, args.app_client_id)
            print(f"updated {ctx.agentcore_json}")
        elif args.command == "owns":
            return 0 if owns_resource(args.name, ctx, known_projects()) else 1
    except CtxError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
