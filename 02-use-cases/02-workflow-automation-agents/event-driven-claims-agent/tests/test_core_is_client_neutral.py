"""Tests that app/04_hesta_member_email/core is the client-neutral core it claims to be.

The core is the part every client's container ships.  Two properties make it pluggable:

1. It names no client and imports nothing from a client package, so a second client adds a package beside it and changes
   nothing in it.
2. It works when ONLY the app directory is present.  The rule-pack loader used to sit in a sibling ``rules/`` folder, which a
   container built from the app directory does not contain.

Run:
    python3 -m unittest tests.test_core_is_client_neutral -v

The self-contained test needs `strands-agents[cedar]` and PyYAML and skips with a reason without them.
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
CORE = APP / "core"
CLIENT_NAMES = ("hesta", "medibank")  # clients we have worked with or discussed: none may appear in the core

# Third-party packages the core may import. Anything else (above all a client package) is a failure.
ALLOWED_THIRD_PARTY = {"core", "strands", "strands_compose", "bedrock_agentcore", "mcp", "yaml", "cedarpy"}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


class CoreIsClientNeutralTests(unittest.TestCase):
    def test_the_core_has_python_files(self):
        self.assertTrue(list(CORE.glob("*.py")), "core/ is empty, so the checks below would pass for no reason")

    def test_no_file_in_the_core_names_a_client(self):
        for path in sorted(CORE.glob("*.py")):
            text = path.read_text(encoding="utf-8").lower()
            for name in CLIENT_NAMES:
                with self.subTest(file=path.name, client=name):
                    self.assertNotIn(name, text)

    def test_the_core_imports_only_the_standard_library_and_allowed_packages(self):
        for path in sorted(CORE.glob("*.py")):
            unexpected = {m for m in _imports(path) if m not in sys.stdlib_module_names and m not in ALLOWED_THIRD_PARTY}
            with self.subTest(file=path.name):
                self.assertEqual(unexpected, set(), f"{path.name} imports {sorted(unexpected)}")

    def test_the_check_would_catch_a_client_import(self):
        # The test above must be able to fail: a core file importing the client package is flagged.
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.py"
            bad.write_text("from client_pkg import schemas\nimport os\n", encoding="utf-8")
            unexpected = {m for m in _imports(bad) if m not in sys.stdlib_module_names and m not in ALLOWED_THIRD_PARTY}
        self.assertEqual(unexpected, {"client_pkg"})

    def test_the_client_package_depends_on_the_core_and_not_the_other_way_round(self):
        client_imports = set().union(*(_imports(p) for p in (APP / "hesta").glob("*.py")))
        self.assertIn("core", client_imports)


SKIP_REASON = ""
try:
    import cedarpy  # noqa: F401
    import yaml  # noqa: F401
    from strands.vended_interventions.cedar import CedarAuthorization  # noqa: F401
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    SKIP_REASON = f"needs strands-agents[cedar] and PyYAML ({exc})"


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class CoreShipsAloneTests(unittest.TestCase):
    def test_the_loader_works_when_only_the_app_directory_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shutil.copytree(APP, tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copytree(ROOT / "rules" / "packs" / "health-insurer-illustrative", tmp_path / "pack_src")
            script = textwrap.dedent("""
                import os, sys, tempfile
                from pathlib import Path
                from core import cedar_pack
                leaked = [p for p in sys.path if p.rstrip("/").endswith("/rules")]
                assert not leaked, f"the repo's rules/ folder is on the path: {leaked}"
                src = Path(os.environ["PACK_SRC"])
                with tempfile.TemporaryDirectory() as work:
                    pack = cedar_pack._fresh_pack(work, "p", src)
                    cedar_pack.load_cedar_authorization(pack, principal={"type": "Agent", "id": "member_enquiry"})
                print("VERIFIED")
            """)
            env = {**os.environ, "PYTHONPATH": str(tmp_path / "app"), "PACK_SRC": str(tmp_path / "pack_src")}
            result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path / "app", env=env,
                                    capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("VERIFIED", result.stdout)


if __name__ == "__main__":
    unittest.main()
