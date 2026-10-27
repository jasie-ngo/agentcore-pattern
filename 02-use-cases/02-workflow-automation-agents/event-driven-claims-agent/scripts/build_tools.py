#!/usr/bin/env python3
"""Build Gateway tools and ingestion settings from each use case's YAML forms.

Reads, for every use case folder under app/ (any folder with a config.yaml):

    app/<use_case>/gateway_tools.yaml   tools (blueprint or custom) + their tables
    app/<use_case>/ingestion.yaml       how inbound messages are cleaned up

and writes (all generated — never edit by hand):

    agentcore/generated/schemas/<tool>.json   tool schemas for blueprint tools
    agentcore/generated/manifest.json         everything CDK needs to build the
                                              Lambdas, tables and Trigger settings
    agentcore/agentcore.json                  the Gateway's `targets` list

Run after editing any of the YAML forms (deploy.sh runs it for you):

    uv run --project app python scripts/build_tools.py          # write
    uv run --project app python scripts/build_tools.py --check  # verify only (CI)

CDK refuses to synthesise if the forms changed since the last build, so a
stale manifest can never be deployed. See docs/FRAMEWORK.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
GENERATED = ROOT / "agentcore" / "generated"
SCHEMA_DIR = GENERATED / "schemas"
MANIFEST = GENERATED / "manifest.json"
AGENTCORE_JSON = ROOT / "agentcore" / "agentcore.json"

BLUEPRINTS = {
    "retrieval": {"code": "lambdas/blueprints/retrieval", "access": "read"},
    "record_write": {"code": "lambdas/blueprints/record_write", "access": "write"},
}
# Shared tables created by the framework's CDK stack (infra-construct.ts).
EXISTING_TABLES = {"Policies", "Claims", "Reviews"}
KEY_TYPES = {"string", "number"}
INPUT_TYPES = {"string", "number", "integer", "boolean", "object", "array"}
CUSTOM_PERMISSIONS = {"ses_send"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TARGET_RE = re.compile(r"^[a-z][a-z0-9-]*$")
MAX_TOOL_CONFIG_BYTES = 3500  # Lambda environment variables share a 4 KB limit


class BuildError(Exception):
    pass


# ─── Inputs ────────────────────────────────────────────────────────────────────


def use_case_dirs() -> list[Path]:
    return sorted(
        p for p in APP.iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "config.yaml").is_file()
    )


def source_files() -> list[Path]:
    """Files whose content decides the build output (also hashed by CDK)."""
    files = [APP / "main.py"]
    for uc in use_case_dirs():
        files += [f for f in (uc / "gateway_tools.yaml", uc / "ingestion.yaml") if f.is_file()]
    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix())


def source_hash() -> str:
    """sha256 over relative path + content (CRLF→LF). Mirrored in infra-construct.ts."""
    h = hashlib.sha256()
    for f in source_files():
        h.update(f.relative_to(ROOT).as_posix().encode() + b"\0")
        h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise BuildError(f"{path.relative_to(ROOT)}: top level must be a mapping")
    return data


def default_use_case() -> str:
    m = re.search(r'^USE_CASE\s*=\s*["\'](\w+)["\']', (APP / "main.py").read_text(encoding="utf-8"), re.MULTILINE)
    if not m:
        raise BuildError('app/main.py: could not find USE_CASE = "<use_case>"')
    return m.group(1)


# ─── Validation + translation ──────────────────────────────────────────────────


def _resolve_tables(uc: str, spec: dict, where: str) -> tuple[dict, list[dict]]:
    """Return ({table key → reference}, [tables to create])."""
    refs: dict[str, dict] = {}
    created: list[dict] = []
    for key, table in (spec.get("tables") or {}).items():
        loc = f"{where} tables.{key}"
        if not NAME_RE.match(key):
            raise BuildError(f"{loc}: table keys must be lowercase snake_case")
        if "existing" in table:
            if table["existing"] not in EXISTING_TABLES:
                raise BuildError(f"{loc}: existing must be one of {sorted(EXISTING_TABLES)}")
            refs[key] = {"existing": table["existing"]}
            continue
        pk = table.get("partition_key") or {}
        if not pk.get("name") or pk.get("type", "string") not in KEY_TYPES:
            raise BuildError(f"{loc}: needs partition_key {{name, type: string|number}}")
        table_id = f"{uc}.{key}"
        created.append(
            {
                "id": table_id,
                "use_case": uc,
                "key": key,
                "partition_key": {"name": pk["name"], "type": pk.get("type", "string")},
                "sort_key": table.get("sort_key"),
                "indexes": table.get("indexes") or [],
            }
        )
        refs[key] = {"created": table_id}
    return refs, created


def _blueprint_tool(tool: dict, loc: str, tables: dict) -> tuple[dict, dict]:
    """Return (manifest fields, tool schema) for a blueprint tool."""
    blueprint = tool["blueprint"]
    if blueprint not in BLUEPRINTS:
        raise BuildError(f"{loc}: blueprint must be one of {sorted(BLUEPRINTS)}")
    table_key = tool.get("table")
    if table_key not in tables:
        raise BuildError(f"{loc}: table '{table_key}' is not declared under tables:")

    properties: dict = {}
    required: list[str] = []
    if blueprint == "retrieval":
        lookup, flt = tool.get("lookup"), tool.get("filter")
        if bool(lookup) == bool(flt):
            raise BuildError(f"{loc}: retrieval needs exactly one of lookup: or filter:")
        if flt and not all(k in flt for k in ("index", "key", "value")):
            raise BuildError(f"{loc}: filter needs index, key and value")
        for entry in lookup or []:
            properties[entry["key"]] = {
                "type": "number" if entry.get("type") == "number" else "string",
                "description": entry.get("description", f"{entry['key']} to look up"),
            }
        if lookup and len(lookup) == 1:
            required = [lookup[0]["key"]]
        config = {k: tool[k] for k in ("lookup", "filter", "returns", "result_key") if tool.get(k) is not None}
        config["lookup"] = [{k: v for k, v in e.items() if k != "description"} for e in config.get("lookup", [])]
        if not config["lookup"]:
            del config["lookup"]
    else:  # record_write
        inputs = tool.get("inputs") or {}
        if not inputs:
            raise BuildError(f"{loc}: record_write needs inputs:")
        for field, spec in inputs.items():
            if spec.get("type", "string") not in INPUT_TYPES:
                raise BuildError(f"{loc}: inputs.{field}.type must be one of {sorted(INPUT_TYPES)}")
            properties[field] = {"type": spec.get("type", "string"), "description": spec.get("description", field)}
            if spec.get("required"):
                required.append(field)
        if tool.get("if_exists", "new_record") not in ("new_record", "return_existing", "error"):
            raise BuildError(f"{loc}: if_exists must be new_record, return_existing or error")
        id_spec = tool.get("id") or {}
        if tool.get("if_exists") in ("return_existing", "error") and not id_spec.get("from"):
            raise BuildError(f"{loc}: if_exists: {tool['if_exists']} needs id.from (fields that identify a record)")
        for field in id_spec.get("from") or []:
            if field not in inputs:
                raise BuildError(f"{loc}: id.from field '{field}' is not one of the inputs")
        config = {
            k: tool[k]
            for k in ("inputs", "id", "if_exists", "rules", "defaults", "set", "derived", "timestamps", "notify")
            if tool.get(k) is not None
        }
        config["inputs"] = {f: {k: v for k, v in s.items() if k != "description"} for f, s in inputs.items()}

    tool_config = json.dumps(config, separators=(",", ":"))
    if len(tool_config.encode()) > MAX_TOOL_CONFIG_BYTES:
        raise BuildError(f"{loc}: tool settings are too large for a Lambda environment variable")

    schema = [
        {
            "name": tool["name"],
            "description": " ".join(tool["description"].split()),
            "inputSchema": {"type": "object", "properties": properties, "required": required},
        }
    ]
    manifest = {
        "kind": "blueprint",
        "blueprint": blueprint,
        "code": BLUEPRINTS[blueprint]["code"],
        "table": tables[table_key],
        "table_access": BLUEPRINTS[blueprint]["access"],
        "tool_config": config,
        "notify": bool(tool.get("notify")),
        "schema_file": f"agentcore/generated/schemas/{tool['name']}.json",
    }
    return manifest, schema


def _custom_tool(tool: dict, loc: str, tables: dict) -> dict:
    custom = tool["custom"]
    code, schema_path = custom.get("code"), custom.get("schema")
    if not code or not (ROOT / code / "handler.py").is_file():
        raise BuildError(f"{loc}: custom.code must be a folder containing handler.py")
    if not schema_path or not (ROOT / schema_path).is_file():
        raise BuildError(f"{loc}: custom.schema must point at the tool's schema JSON")
    names = [t.get("name") for t in json.loads((ROOT / schema_path).read_text(encoding="utf-8"))]
    if tool["name"] not in names:
        raise BuildError(f"{loc}: {schema_path} does not define a tool named '{tool['name']}'")

    environment: dict = {}
    for var, spec in (custom.get("environment") or {}).items():
        if "table" in spec:
            if spec["table"] not in tables:
                raise BuildError(f"{loc}: environment.{var}: table '{spec['table']}' is not declared")
            environment[var] = {"table": tables[spec["table"]]}
        elif "env" in spec:
            environment[var] = {"env": spec["env"], "default": spec.get("default", "")}
        elif "value" in spec:
            environment[var] = {"value": str(spec["value"])}
        else:
            raise BuildError(f"{loc}: environment.{var} needs table, env or value")

    grants = []
    for key, access in (custom.get("tables") or {}).items():
        if key not in tables or access not in ("read", "write"):
            raise BuildError(f"{loc}: tables.{key} must be a declared table with read|write")
        grants.append({"table": tables[key], "access": access})
    permissions = custom.get("permissions") or []
    unknown = set(permissions) - CUSTOM_PERMISSIONS
    if unknown:
        raise BuildError(f"{loc}: unknown permissions {sorted(unknown)} (known: {sorted(CUSTOM_PERMISSIONS)})")

    return {
        "kind": "custom",
        "code": code,
        "environment": environment,
        "table_grants": grants,
        "permissions": permissions,
        "schema_file": schema_path,
    }


def build() -> tuple[dict, dict[str, list], dict]:
    """Return (manifest, {schema file → content}, agentcore.json content)."""
    tools: list[dict] = []
    schemas: dict[str, list] = {}
    created_tables: list[dict] = []
    ingestion: dict[str, dict] = {}
    uses: dict[str, list[str]] = {}

    for uc_dir in use_case_dirs():
        uc = uc_dir.name
        where = f"app/{uc}/gateway_tools.yaml"
        spec = load_yaml(uc_dir / "gateway_tools.yaml")
        ingestion[uc] = load_yaml(uc_dir / "ingestion.yaml")
        uses[uc] = spec.get("uses") or []
        tables, created = _resolve_tables(uc, spec, where)
        created_tables += created

        for i, tool in enumerate(spec.get("tools") or []):
            loc = f"{where} tools[{i}] ({tool.get('name', '?')})"
            name = tool.get("name", "")
            if not NAME_RE.match(name):
                raise BuildError(f"{loc}: name must be lowercase snake_case")
            target = tool.get("target") or name.replace("_", "-")
            if not TARGET_RE.match(target):
                raise BuildError(f"{loc}: target must be lowercase-with-hyphens")
            if not tool.get("description"):
                raise BuildError(f"{loc}: description is required (the agent reads it)")
            if ("blueprint" in tool) == ("custom" in tool):
                raise BuildError(f"{loc}: set exactly one of blueprint: or custom:")

            if "blueprint" in tool:
                fields, schema = _blueprint_tool(tool, loc, tables)
                schemas[fields["schema_file"]] = schema
            else:
                fields = _custom_tool(tool, loc, tables)
            tools.append({"name": name, "target": target, "use_case": uc, **fields})

    for attr in ("name", "target"):
        seen: dict[str, str] = {}
        for tool in tools:
            if tool[attr] in seen:
                raise BuildError(
                    f"tool {attr} '{tool[attr]}' is declared by both {seen[tool[attr]]} and {tool['use_case']} "
                    "— tool names and targets must be unique across use cases"
                )
            seen[tool[attr]] = tool["use_case"]
    declared = {t["name"] for t in tools}
    for uc, names in uses.items():
        missing = sorted(set(names) - declared)
        if missing:
            raise BuildError(f"app/{uc}/gateway_tools.yaml uses: {missing} are not declared by any use case")

    manifest = {
        "_generated": "by scripts/build_tools.py from app/*/gateway_tools.yaml and app/*/ingestion.yaml — do not edit",
        "source_hash": source_hash(),
        "default_use_case": default_use_case(),
        "tables": created_tables,
        "tools": tools,
        "ingestion": ingestion,
    }

    agentcore = json.loads(AGENTCORE_JSON.read_text(encoding="utf-8"))
    gateways = agentcore.get("agentCoreGateways") or []
    if len(gateways) != 1:
        raise BuildError("agentcore/agentcore.json must declare exactly one Gateway")
    gateways[0]["targets"] = [
        {
            "name": t["target"],
            "targetType": "lambdaFunctionArn",
            "lambdaFunctionArn": {
                "lambdaArn": "PLACEHOLDER_" + t["target"].upper().replace("-", "_"),
                "toolSchemaFile": t["schema_file"],
            },
        }
        for t in tools
    ]
    return manifest, schemas, agentcore


# ─── Output ────────────────────────────────────────────────────────────────────


def _json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def outputs() -> dict[Path, str]:
    manifest, schemas, agentcore = build()
    files = {MANIFEST: _json(manifest), AGENTCORE_JSON: _json(agentcore)}
    for rel, schema in schemas.items():
        files[ROOT / rel] = _json(schema)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Verify generated files are up to date; write nothing.")
    args = parser.parse_args()

    try:
        files = outputs()
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    expected_schemas = {p for p in files if p.parent == SCHEMA_DIR}
    stale_schemas = set(SCHEMA_DIR.glob("*.json")) - expected_schemas if SCHEMA_DIR.is_dir() else set()
    changed = [p for p, text in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]

    if args.check:
        problems = [p.relative_to(ROOT).as_posix() for p in changed + sorted(stale_schemas)]
        if problems:
            print(
                "OUT OF DATE: generated files differ from the YAML forms - run: uv run --project app python scripts/build_tools.py"
            )
            for p in problems:
                print(f"  - {p}")
            return 1
        print("OK: generated tool files are up to date")
        return 0

    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for path in changed:
        path.write_text(files[path], encoding="utf-8", newline="\n")
    for path in stale_schemas:
        path.unlink()
    manifest = json.loads(files[MANIFEST])
    print(f"OK: {len(manifest['tools'])} tools from {len(manifest['ingestion'])} use cases")
    for t in manifest["tools"]:
        kind = t["blueprint"] if t["kind"] == "blueprint" else "custom"
        print(f"  {t['use_case']:14s} {t['name']:22s} -> {t['target']:20s} ({kind})")
    print(f"  {len(changed)} file(s) updated, {len(stale_schemas)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
