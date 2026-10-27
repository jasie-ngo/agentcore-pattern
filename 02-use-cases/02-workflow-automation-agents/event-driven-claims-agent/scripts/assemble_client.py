#!/usr/bin/env python3
"""Assemble a deployable project for ONE client from the core and that client's folder.  ADR-0015, Q7.

    python3 scripts/assemble_client.py acme --display-name "Acme Super" --profile presale
    python3 scripts/assemble_client.py acme --pack-dir clients/acme/pack --enable-pack

Why a project per client: the AgentCore CLI keeps its deployment state inside the project directory, and the project name
decides the names of the gateway, memory, evaluator, runtime and image repository.  Two clients sharing one project would share
state and collide on every one of those names.  So each client gets its own, in build/<client>/ (git-ignored):

    build/<client>/
        agentcore/agentcore.json     rendered: the client's project, memory and credential names; placeholders for the identity
                                     (a client must never inherit another environment's Cognito pool)
        agentcore/cdk/               the CDK app, with cdk.json set to this client (names, key, tags: see lib/naming.ts)
        app/04_hesta_member_email/   the core and the client package, plus client_pack/ if the client has a rule pack
        lambdas/                     the tool functions

Nothing here deploys anything or contacts AWS.  A rule pack is copied into the image but only USED when --enable-pack sets
CEDAR_PACK_DIR, and that is refused unless the pack verifies (an unapproved pack stops the agent starting, so it must not ship on).

Standard library only (the optional --enable-pack check imports the core loader, which needs PyYAML).
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_REL = Path("app") / "04_hesta_member_email"
MARKER = ".assembled-by-claimscore"
PACK_DIR_IN_IMAGE = "/app/client_pack"
# Well formed, because `agentcore validate` insists on the shape, and obviously fake: no such pool exists, so a deploy that
# forgot to replace it fails at the identity step instead of trusting someone else's pool.
PLACEHOLDER_DISCOVERY = "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_PLACEHOLDER/.well-known/openid-configuration"
PLACEHOLDER_CLIENT_ID = "PLACEHOLDER_CLIENT_ID"

# Must match agentcore/cdk/lib/naming.ts (a test compares the two over a list of slugs).
_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,22}[a-z0-9]$")
_RESERVED = {"claimsagent", "agentcore", "aws", "amazon"}
_IGNORE = shutil.ignore_patterns("node_modules", "dist", "cdk.out", "__pycache__", "*.pyc", ".venv", "client_pack", ".DS_Store")


class AssembleError(Exception):
    """A readable reason the project could not be assembled."""


def parse_slug(raw: str) -> str:
    value = (raw or "").strip()
    if not _SLUG.match(value) or "--" in value or value in _RESERVED:
        raise AssembleError(
            f'Invalid client "{value}". Use 3 to 24 lower-case letters, digits and single hyphens, starting with a letter and '
            f"not ending in a hyphen (not one of: {', '.join(sorted(_RESERVED))})."
        )
    return value


def project_name(slug: str) -> str:
    """The AgentCore project name: letters and digits only (it is used inside names that allow no hyphen). fund-a1 -> FundA1."""
    return "".join(part.capitalize() for part in slug.split("-"))


def _set_env(runtime: dict, name: str, value: str) -> None:
    for entry in runtime["envVars"]:
        if entry["name"] == name:
            entry["value"] = value
            return
    runtime["envVars"].append({"name": name, "value": value})


def render_spec(base: dict, slug: str, *, profile: str, discovery_url: str | None, client_id: str | None, enable_pack: bool) -> dict:
    """The client's agentcore.json: the base with this client's names, and the base's identity replaced by placeholders."""
    spec = copy.deepcopy(base)
    project = project_name(slug)
    old_project, old_credential = spec["name"], spec["credentials"][0]["name"]
    base_discovery = spec["credentials"][0].get("discoveryUrl", "")
    base_clients = [c for g in spec.get("agentCoreGateways", []) for c in g.get("authorizerConfiguration", {}).get("customJwtAuthorizer", {}).get("allowedClients", [])]

    spec["name"] = project
    for memory in spec["memories"]:
        memory["name"] = f"{project}{memory['name'][len(old_project):]}" if memory["name"].startswith(old_project) else f"{project}{memory['name']}"

    credential = f"{slug}-gateway-m2m"
    spec["credentials"][0]["name"] = credential
    spec["credentials"][0]["discoveryUrl"] = discovery_url or PLACEHOLDER_DISCOVERY
    for gateway in spec.get("agentCoreGateways", []):
        jwt = gateway.get("authorizerConfiguration", {}).get("customJwtAuthorizer")
        if jwt is not None:
            jwt["discoveryUrl"] = discovery_url or PLACEHOLDER_DISCOVERY
            jwt["allowedClients"] = [client_id or PLACEHOLDER_CLIENT_ID]

    runtime = spec["runtimes"][0]
    _set_env(runtime, "AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER", credential)
    _set_env(runtime, "DEPLOYMENT_PROFILE", profile)
    if enable_pack:
        _set_env(runtime, "CEDAR_PACK_DIR", PACK_DIR_IN_IMAGE)

    # Guard: nothing of the base's environment may survive into a client's project.
    rendered = json.dumps(spec)
    leaks = [x for x in [old_credential, *base_clients, *re.findall(r"ap-southeast-2_\w+", base_discovery)] if x and x in rendered]
    if client_id and client_id in leaks:
        leaks.remove(client_id)
    if leaks:
        raise AssembleError(f"the base project's own identifiers would leak into {slug}'s project: {leaks}")
    return spec


def _verify_pack(root: Path, pack_dir: Path) -> None:
    sys.path.insert(0, str(root / APP_REL))
    try:
        from core import cedar_pack  # noqa: PLC0415 - needs PyYAML, only for --enable-pack
    except ImportError as exc:
        raise AssembleError(f"--enable-pack needs PyYAML and the core loader to verify the pack ({exc}).") from exc
    try:
        cedar_pack.verify_pack(pack_dir)
    except cedar_pack.PackRejected as exc:
        raise AssembleError(
            f"--enable-pack refused: the pack would not load, so the agent would not start. {exc}. "
            "Approve it first (see clients/SME-START-HERE.md), or assemble without --enable-pack."
        ) from exc


def assemble(
    slug: str,
    out: Path | None = None,
    *,
    display_name: str | None = None,
    profile: str = "strict",
    discovery_url: str | None = None,
    client_id: str | None = None,
    pack_dir: Path | None = None,
    enable_pack: bool = False,
    link_node_modules: bool = False,
    force: bool = False,
    root: Path = ROOT,
) -> dict:
    slug = parse_slug(slug)
    if profile not in ("strict", "presale"):
        raise AssembleError(f'--profile must be "strict" or "presale", not "{profile}"')
    out = (out or root / "build" / slug).resolve()
    pack_dir = (pack_dir or root / "clients" / slug / "pack").resolve()
    has_pack = pack_dir.is_dir()
    if has_pack and not (pack_dir / "manifest.json").is_file():
        raise AssembleError(f"{pack_dir} has no manifest.json. Compile it first: python3 rules/cedar_pack.py compile {pack_dir}")
    if enable_pack and not has_pack:
        raise AssembleError(f"--enable-pack was given but there is no pack at {pack_dir}")
    if enable_pack:
        _verify_pack(root, pack_dir)

    if out.exists():
        if not force:
            raise AssembleError(f"{out} already exists. Use --force to rebuild it.")
        if not (out / MARKER).is_file():
            raise AssembleError(f"{out} was not made by this tool (no {MARKER}), so it will not be replaced.")
        shutil.rmtree(out)

    base = json.loads((root / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
    spec = render_spec(base, slug, profile=profile, discovery_url=discovery_url, client_id=client_id, enable_pack=enable_pack)

    out.mkdir(parents=True)
    shutil.copytree(root / "agentcore" / "cdk", out / "agentcore" / "cdk", ignore=_IGNORE)
    shutil.copy2(root / "agentcore" / "aws-targets.json.template", out / "agentcore" / "aws-targets.json.template")
    shutil.copytree(root / "lambdas", out / "lambdas", ignore=_IGNORE)
    shutil.copytree(root / APP_REL, out / APP_REL, ignore=_IGNORE)
    (out / "agentcore" / "agentcore.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    cdk_json = out / "agentcore" / "cdk" / "cdk.json"
    cdk = json.loads(cdk_json.read_text(encoding="utf-8"))
    cdk.setdefault("context", {})["client"] = slug
    if display_name:
        cdk["context"]["clientName"] = display_name
    cdk_json.write_text(json.dumps(cdk, indent=2) + "\n", encoding="utf-8")

    if has_pack:
        shutil.copytree(pack_dir, out / APP_REL / "client_pack", ignore=_IGNORE)
    if link_node_modules and (root / "agentcore" / "cdk" / "node_modules").is_dir():
        (out / "agentcore" / "cdk" / "node_modules").symlink_to(root / "agentcore" / "cdk" / "node_modules")

    info = {
        "client": slug,
        "project": spec["name"],
        "display_name": display_name,
        "profile": profile,
        "credential_provider": spec["credentials"][0]["name"],
        "pack_included": has_pack,
        "pack_enabled": enable_pack,
        "source_commit": _commit(root),
    }
    (out / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    (out / "README-BUILD.md").write_text(_readme(info), encoding="utf-8")
    (out / MARKER).write_text("made by scripts/assemble_client.py; safe to rebuild with --force\n", encoding="utf-8")
    return {**info, "out": str(out)}


def _commit(root: Path) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _readme(info: dict) -> str:
    pack = (
        "The client's rule pack is in the image and ON (CEDAR_PACK_DIR is set)." if info["pack_enabled"]
        else "The client's rule pack is in the image but OFF." if info["pack_included"]
        else "There is no rule pack for this client."
    )
    return f"""# {info['client']}: assembled project (profile: {info['profile']})

Made by `scripts/assemble_client.py` from the core at commit {info['source_commit']}. Do not edit by hand: rebuild with
`--force`. Nothing here has been deployed.

{pack}

## Before you deploy

- **Profile `{info['profile']}`.** `presale` is for synthetic or de-identified data only (ADR-0015). Real member data needs
  `strict` and the open items in that record (region, retention, signed approvals, the SES confirmation).
- **Identity is a placeholder.** `agentcore/agentcore.json` carries `{PLACEHOLDER_DISCOVERY}` and `{PLACEHOLDER_CLIENT_ID}`.
  Create this client's own Cognito pool and put its values in, or set `COGNITO_DISCOVERY_URL` and
  `AGENTCORE_GATEWAY_CLIENT_ID` for the build. Never reuse another client's pool.

## Steps

```bash
cd agentcore/cdk && npm ci && cd ../..
cp agentcore/aws-targets.json.template agentcore/aws-targets.json   # then set this client's account and region
export AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER={info['credential_provider']}
agentcore validate
agentcore deploy --target dev
```

The CDK app is already set to client `{info['client']}` in `agentcore/cdk/cdk.json`, so every resource name carries it.

The repository's scripts can drive this project for you. Run them from the repository root, with this project at `build/{info['client']}`
(or pass `--project-dir`):

```bash
./deploy.sh <region> --client {info['client']} --plan     # print every name it would use; nothing is changed
./deploy.sh <region> --client {info['client']}            # Cognito pool, credential, deploy, seed
./scripts/destroy.sh <region> --client {info['client']}   # this client's stack, credential and Cognito pool only
```

The steps above do the same by hand. Nothing has been run against AWS yet.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Assemble a deployable project for one client.", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("client", help="the client slug: 3 to 24 lower-case letters, digits and hyphens")
    ap.add_argument("--display-name", help="how the client is named in end-user text (the guardrail's messages)")
    ap.add_argument("--profile", default="strict", choices=["strict", "presale"], help="deployment profile (default: strict)")
    ap.add_argument("--out", type=Path, help="where to write the project (default: build/<client>)")
    ap.add_argument("--pack-dir", type=Path, help="the client's compiled rule pack (default: clients/<client>/pack)")
    ap.add_argument("--enable-pack", action="store_true", help="switch the pack on in the image; refused unless the pack verifies")
    ap.add_argument("--discovery-url", help="this client's Cognito OIDC discovery URL (default: a placeholder)")
    ap.add_argument("--client-id", help="this client's Cognito app client id (default: a placeholder)")
    ap.add_argument("--link-node-modules", action="store_true", help="symlink the CDK dependencies instead of running npm ci (dev and tests)")
    ap.add_argument("--force", action="store_true", help="rebuild a directory this tool made earlier")
    args = ap.parse_args()
    try:
        result = assemble(
            args.client, args.out, display_name=args.display_name, profile=args.profile, discovery_url=args.discovery_url,
            client_id=args.client_id, pack_dir=args.pack_dir, enable_pack=args.enable_pack, link_node_modules=args.link_node_modules,
            force=args.force,
        )
    except AssembleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"assembled {result['client']} -> {result['out']}")
    print(f"  project {result['project']} | profile {result['profile']} | pack: {'on' if result['pack_enabled'] else 'in image, off' if result['pack_included'] else 'none'}")
    print(f"  next: see {result['out']}/README-BUILD.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
