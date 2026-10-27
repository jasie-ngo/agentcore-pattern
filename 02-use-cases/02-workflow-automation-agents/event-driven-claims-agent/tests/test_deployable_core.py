"""The deployable core: the base agentcore.json deploys the YAML port, and what it deploys is buildable and pinned.

These are cheap, offline checks on the files that make the core deployable (ADR-0015, Phase 5). The real proofs (the image builds
and answers /ping, the project validates, two assembled clients share no name) were run against Docker and the CDK; the checks
here keep the cheap parts from drifting.

Run:
    python3 -m unittest tests.test_deployable_core -v
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
SPEC = json.loads((ROOT / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
RUNTIME = SPEC["runtimes"][0]
ENV = {e["name"]: e["value"] for e in RUNTIME["envVars"]}
DOCKERFILE = (APP / "Dockerfile").read_text(encoding="utf-8")
CONFIG = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))


def _default(expression: str) -> str:
    match = re.fullmatch(r"\$\{[A-Z_]+:-(.+)\}", expression)
    assert match, expression
    return match.group(1)


class BaseSpecTests(unittest.TestCase):
    def test_the_base_spec_deploys_the_yaml_port_as_a_container(self):
        self.assertEqual(RUNTIME["build"], "Container")
        self.assertEqual(RUNTIME["codeLocation"], "app/04_hesta_member_email")
        self.assertEqual(RUNTIME["entrypoint"], "main.py")

    def test_the_code_location_has_everything_the_build_needs(self):
        code = ROOT / RUNTIME["codeLocation"]
        for name in ("Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "main.py", "config.yaml", "core", "hesta"):
            self.assertTrue((code / name).exists(), name)

    def test_the_online_evaluation_targets_the_runtime_that_exists(self):
        for config in SPEC["onlineEvalConfigs"]:
            self.assertEqual(config["agent"], RUNTIME["name"])

    def test_the_models_in_the_spec_are_the_pinned_ones(self):
        # agentcore.json sets these variables, and an environment variable overrides the config's default at runtime, so the pin
        # in tests/test_model_pin.py is only real if this agrees with it.
        self.assertEqual(ENV["AGENT_MODEL_ID"], _default(CONFIG["vars"]["primary_model"]))
        self.assertEqual(ENV["FAST_MODEL_ID"], _default(CONFIG["vars"]["fast_model"]))

    def test_the_base_is_strict_and_the_pack_is_off(self):
        self.assertEqual(ENV["DEPLOYMENT_PROFILE"], "strict")
        self.assertNotIn("CEDAR_PACK_DIR", ENV)

    def test_the_old_apps_settings_are_gone(self):
        self.assertNotIn("AUTO_APPROVE_THRESHOLD", ENV)  # read only by the app this no longer deploys


class DockerfileTests(unittest.TestCase):
    def test_dependencies_come_from_the_lockfile_exactly(self):
        self.assertIn("uv sync --frozen", DOCKERFILE)
        self.assertIn("--extra cedar", DOCKERFILE)  # the loader needs it whenever a client's pack is switched on

    def test_it_runs_as_a_non_root_user_with_a_health_check(self):
        self.assertRegex(DOCKERFILE, r"(?m)^USER appuser$")
        self.assertIn("/ping", DOCKERFILE)

    def test_it_starts_the_entrypoint_with_otel_instrumentation(self):
        self.assertIn('CMD ["opentelemetry-instrument", "python", "main.py"]', DOCKERFILE)

    def test_a_host_virtualenv_can_not_reach_the_image(self):
        ignore = (APP / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".venv/", ignore)

    def test_the_entrypoint_builds_the_app_from_the_config(self):
        main = (APP / "main.py").read_text(encoding="utf-8")
        self.assertIn("create_app(", main)
        self.assertIn("config.yaml", main)


class LockfileTests(unittest.TestCase):
    @staticmethod
    def _locked() -> set:
        return set(re.findall(r'(?m)^name = "([^"]+)"$', (APP / "uv.lock").read_text(encoding="utf-8")))

    def test_what_the_container_runs_is_in_the_lockfile(self):
        locked = self._locked()
        for package in ("strands-compose-agentcore", "strands-agents", "bedrock-agentcore", "mcp", "aws-opentelemetry-distro", "cedarpy", "pyyaml"):
            with self.subTest(package=package):
                self.assertIn(package, locked)

    @unittest.skipIf(shutil.which("uv") is None, "needs uv")
    def test_the_lockfile_is_in_step_with_pyproject(self):
        result = subprocess.run(["uv", "lock", "--check"], cwd=APP, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
