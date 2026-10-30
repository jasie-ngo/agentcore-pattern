"""The pipeline overlay: a client's agents merged onto the client-neutral base, and only that client's code shipped.

What is proved:
  * The neutral base names no client, carries the pinned models, and agrees with the first client's config.yaml on everything shared,
    so the two cannot drift apart.
  * The merge refuses an overlay that redefines what the base owns, is incomplete, points at another client's package, or names
    another client anywhere in its text (the copy-paste guard).
  * An assembled client's image holds the core and its own package only: no other client's package, no sample emails, no other
    client's name in its config.
  * With no overlay the build is unchanged (the whole app folder) and says so loudly.

Offline. Needs PyYAML (the merge cannot read YAML without it); the schema check also needs strands-compose and skips without it.

Run:
    python3 -m unittest tests.test_assemble_pipeline -v
"""

import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_REL = Path("app") / "04_hesta_member_email"
APP = ROOT / APP_REL
BASE_FILE = APP / "core" / "pipeline.base.yaml"
CLIENT_NAMES = ("hesta", "medibank")  # clients we have worked with or discussed: none may appear in the neutral base

_spec = importlib.util.spec_from_file_location("assemble_client", ROOT / "scripts" / "assemble_client.py")
assemble_client = importlib.util.module_from_spec(_spec)
sys.modules["assemble_client"] = assemble_client
_spec.loader.exec_module(assemble_client)

try:
    import yaml
except ImportError:  # pragma: no cover - depends on the interpreter
    yaml = None
SKIP_YAML = "needs PyYAML" if yaml is None else ""
pm = assemble_client._pipeline_merge() if yaml is not None else None

# The models are pinned (tests/test_model_pin.py); the base must carry the same defaults.
PINNED = {
    "primary_model": "global.anthropic.claude-sonnet-4-6",
    "fast_model": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}
SHARED_VARS = ("primary_model", "fast_model", "guardrail_sentinel", "profile")
PACKAGE = "acme_insurance"


def _overlay(**changes) -> dict:
    """A made-up insurer's pipeline. Nothing here is a real client's."""
    overlay = {
        "package": PACKAGE,
        "vars": {"claim_threshold": "${CLAIM_THRESHOLD:-70}"},
        "agents": {
            "intake": {
                "type": f"./{PACKAGE}/factory.py:acme_agent",
                "model": "fast",
                "description": "Classify why the policyholder is writing.",
                "tools": [f"./{PACKAGE}/tools.py:lookup_policy"],
                "system_prompt": "You classify a claims email.\nThreshold: ${claim_threshold}. Profile: ${profile}.\n",
            },
            "coordinator": {
                "type": f"./{PACKAGE}/factory.py:acme_agent",
                "model": "primary",
                "description": "Run the pipeline over one email.",
                "system_prompt": "You run the pipeline.\n",
            },
        },
        "orchestrations": {
            "main": {
                "mode": "delegate",
                "entry_name": "coordinator",
                "connections": [{"agent": "intake", "description": "Classify the email.", "preserve_context": False}],
            }
        },
        "entry": "main",
    }
    overlay.update(changes)
    return overlay


def _write_overlay(path: Path, overlay: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(overlay, sort_keys=False), encoding="utf-8")
    return path


def _make_app(root: Path, *, with_other_client: bool = True) -> Path:
    """A small app folder: the core, the first client's package (optional) and the synthetic insurer's."""
    app = root / APP_REL
    (app / "core").mkdir(parents=True)
    shutil.copy2(BASE_FILE, app / "core" / "pipeline.base.yaml")
    (app / "core" / "factory.py").write_text("# core\n", encoding="utf-8")  # core is never a client package
    (app / PACKAGE).mkdir()
    (app / PACKAGE / "factory.py").write_text("# the insurer's package\n", encoding="utf-8")
    if with_other_client:
        (app / "hesta").mkdir()
        (app / "hesta" / "factory.py").write_text("# the first client's package\n", encoding="utf-8")
        (app / "sample_emails").mkdir()
        (app / "sample_emails" / "one.json").write_text("{}", encoding="utf-8")
    (app / "config.yaml").write_text("# the first client's config\nversion: '1'\n", encoding="utf-8")
    return app


@unittest.skipIf(SKIP_YAML, SKIP_YAML)
class BaseTests(unittest.TestCase):
    def setUp(self):
        self.base = yaml.safe_load(BASE_FILE.read_text(encoding="utf-8"))
        self.first = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))

    def test_the_base_names_no_client(self):
        text = BASE_FILE.read_text(encoding="utf-8").lower()
        for name in CLIENT_NAMES:
            with self.subTest(client=name):
                self.assertNotIn(name, text)

    def test_the_base_carries_the_pinned_models(self):
        for name, pinned in PINNED.items():
            with self.subTest(variable=name):
                match = re.fullmatch(r"\$\{[A-Z_]+:-(.+)\}", self.base["vars"][name])
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), pinned)

    def test_the_base_and_the_first_clients_config_agree_on_everything_shared(self):
        # One of them is hand-maintained for now, so this is what stops them drifting: a model default changed in one place only.
        self.assertEqual(self.base["models"], self.first["models"])
        for name in SHARED_VARS:
            with self.subTest(variable=name):
                self.assertEqual(self.base["vars"][name], self.first["vars"][name])
        self.assertEqual((self.base["version"], self.base["log_level"]), (self.first["version"], self.first["log_level"]))


@unittest.skipIf(SKIP_YAML, SKIP_YAML)
class MergeTests(unittest.TestCase):
    def setUp(self):
        self.base = yaml.safe_load(BASE_FILE.read_text(encoding="utf-8"))

    def test_the_overlay_is_laid_over_the_base(self):
        merged = pm.merge_pipeline(self.base, _overlay())
        self.assertEqual(set(merged["models"]), {"primary", "fast", "guarded"})
        self.assertEqual(set(merged["agents"]), {"intake", "coordinator"})
        self.assertIn("claim_threshold", merged["vars"])
        self.assertEqual(merged["vars"]["primary_model"], self.base["vars"]["primary_model"])

    def test_the_base_is_not_modified(self):
        before = json.dumps(self.base, sort_keys=True)
        pm.merge_pipeline(self.base, _overlay())
        self.assertEqual(json.dumps(self.base, sort_keys=True), before)

    def test_an_overlay_may_add_a_model_alias_but_not_redefine_one(self):
        merged = pm.merge_pipeline(self.base, _overlay(models={"large": {"provider": "bedrock", "model_id": "x"}}))
        self.assertIn("large", merged["models"])
        with self.assertRaisesRegex(pm.PipelineError, r"redefines models\.fast"):
            pm.merge_pipeline(self.base, _overlay(models={"fast": {"provider": "bedrock", "model_id": "x"}}))

    def test_an_overlay_may_not_change_a_pinned_variable(self):
        with self.assertRaisesRegex(pm.PipelineError, r"redefines vars\.primary_model"):
            pm.merge_pipeline(self.base, _overlay(vars={"primary_model": "some-other-model"}))

    def test_unknown_and_missing_keys_are_refused(self):
        with self.assertRaisesRegex(pm.PipelineError, "unknown key"):
            pm.merge_pipeline(self.base, _overlay(log_level="DEBUG"))
        incomplete = _overlay()
        del incomplete["entry"]
        with self.assertRaisesRegex(pm.PipelineError, "missing"):
            pm.merge_pipeline(self.base, incomplete)


@unittest.skipIf(SKIP_YAML, SKIP_YAML)
class ValidateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(Path(self._tmp.name))
        self.base = yaml.safe_load(BASE_FILE.read_text(encoding="utf-8"))

    def check(self, overlay: dict, text: str | None = None):
        merged = pm.merge_pipeline(self.base, overlay)
        pm.validate_pipeline(merged, package=overlay["package"], app_dir=self.app, overlay_text=text if text is not None else yaml.safe_dump(overlay))

    def test_a_good_overlay_passes(self):
        self.check(_overlay())

    def test_a_path_into_another_clients_package_is_refused(self):
        overlay = _overlay()
        overlay["agents"]["intake"]["tools"] = ["./hesta/tools.py:classify_verification"]
        with self.assertRaisesRegex(pm.PipelineError, r"agents\.intake\.tools .*must be a \./acme_insurance/"):
            self.check(overlay, text="no mention of the other package here")

    def test_naming_another_client_anywhere_in_the_overlay_is_refused(self):
        # The copy-paste guard: a prompt copied from the first client still says its name.
        overlay = _overlay()
        overlay["agents"]["coordinator"]["system_prompt"] = "You run HESTA's member-email pipeline.\n"
        with self.assertRaisesRegex(pm.PipelineError, 'mentions "hesta", another client'):
            self.check(overlay)

    def test_a_model_must_be_an_alias_and_never_an_id(self):
        overlay = _overlay()
        overlay["agents"]["intake"]["model"] = "no-such-alias"
        overlay["agents"]["coordinator"]["model_id"] = "global.anthropic.claude-opus"
        with self.assertRaises(pm.PipelineError) as ctx:
            self.check(overlay)
        self.assertIn("no-such-alias", str(ctx.exception))
        self.assertIn("sets model_id", str(ctx.exception))

    def test_the_orchestration_must_refer_to_real_agents(self):
        overlay = _overlay()
        overlay["orchestrations"]["main"]["entry_name"] = "ghost"
        overlay["orchestrations"]["main"]["connections"].append({"agent": "phantom", "description": "x"})
        overlay["entry"] = "nope"
        with self.assertRaises(pm.PipelineError) as ctx:
            self.check(overlay)
        for expected in ("entry 'nope'", "entry_name 'ghost'", "connects 'phantom'"):
            self.assertIn(expected, str(ctx.exception))

    def test_the_package_must_exist_and_may_not_be_the_core(self):
        with self.assertRaisesRegex(pm.PipelineError, "does not exist: write the client package first"):
            self.check(_overlay(package="not_written_yet"))
        with self.assertRaisesRegex(pm.PipelineError, "other than \"core\""):
            self.check(_overlay(package="core"))

    def test_the_template_overlay_stops_the_build_until_a_package_is_set(self):
        # The README promises this: copying the template unchanged must not produce a build.
        template, text = pm.read_yaml(ROOT / "clients" / "_template" / "pipeline" / "pipeline.yaml")
        merged = pm.merge_pipeline(self.base, template)  # structurally fine...
        with self.assertRaisesRegex(pm.PipelineError, "write the client package first"):  # ...but the package is a placeholder
            pm.validate_pipeline(merged, package=template["package"], app_dir=self.app, overlay_text=text)

    def test_every_problem_is_reported_at_once(self):
        overlay = _overlay(package="not_written_yet")
        overlay["agents"]["intake"]["model"] = "no-such-alias"
        with self.assertRaises(pm.PipelineError) as ctx:
            self.check(overlay)
        message = str(ctx.exception)
        self.assertIn("not_written_yet", message)
        self.assertIn("no-such-alias", message)
        self.assertGreaterEqual(message.count("\n - "), 3)


@unittest.skipIf(SKIP_YAML, SKIP_YAML)
class DumpTests(unittest.TestCase):
    def test_the_written_yaml_reads_back_exactly_and_keeps_prompts_as_blocks(self):
        base = yaml.safe_load(BASE_FILE.read_text(encoding="utf-8"))
        merged = pm.merge_pipeline(base, _overlay())
        text = pm.dump_pipeline(merged)
        self.assertEqual(yaml.safe_load(text), merged)
        self.assertTrue(text.startswith("# GENERATED"))
        self.assertIn("system_prompt: |", text)  # readable, not one escaped line

    def test_awkward_strings_survive(self):
        # Trailing spaces, tabs, a leading space and unicode are the cases where a block scalar cannot keep the exact string.
        prompt = "line with trailing space \n\tindented by a tab\n  leading spaces\ncurly ’ and — dash\n"
        merged = {"agents": {"a": {"system_prompt": prompt}}}
        self.assertEqual(yaml.safe_load(pm.dump_pipeline(merged))["agents"]["a"]["system_prompt"], prompt)


@unittest.skipIf(SKIP_YAML, SKIP_YAML)
class AssembleWithOverlayTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # A root with the real CDK app and lambdas beside a small app folder of this test's own, so nothing real is touched.
        self.root = self.tmp / "root"
        self.root.mkdir()
        for rel in ("agentcore", "lambdas"):
            (self.root / rel).symlink_to(ROOT / rel, target_is_directory=True)
        self.app = _make_app(self.root)
        self.overlay_path = _write_overlay(self.root / "clients" / "acme" / "pipeline" / "pipeline.yaml", _overlay())

    def assemble(self, name="out", **kwargs):
        kwargs.setdefault("pack_dir", self.tmp / "no-pack")
        return assemble_client.assemble("acme", self.tmp / name, root=self.root, **kwargs)

    def test_the_image_holds_the_core_and_this_clients_package_only(self):
        info = self.assemble()
        out_app = Path(info["out"]) / APP_REL
        self.assertTrue((out_app / "core").is_dir())
        self.assertTrue((out_app / PACKAGE / "factory.py").is_file())
        self.assertFalse((out_app / "hesta").exists())
        self.assertFalse((out_app / "sample_emails").exists())
        self.assertTrue((info["pipeline_overlay"], info["pipeline_package"]) == (True, PACKAGE))

    def test_the_shipped_config_is_the_merge_and_names_no_other_client(self):
        out_app = Path(self.assemble()["out"]) / APP_REL
        text = (out_app / "config.yaml").read_text(encoding="utf-8")
        base = yaml.safe_load(BASE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(yaml.safe_load(text), pm.merge_pipeline(base, _overlay()))
        self.assertNotIn("hesta", text.lower())
        self.assertNotIn("first client's config", text)  # the copied config was replaced, not appended to

    def test_nothing_that_ships_has_another_clients_name_in_its_path(self):
        out_app = Path(self.assemble()["out"]) / APP_REL
        offenders = [p.relative_to(out_app).as_posix() for p in out_app.rglob("*") if "hesta" in p.relative_to(out_app).as_posix().lower()]
        self.assertEqual(offenders, [])

    def test_the_source_app_folder_is_left_alone(self):
        self.assemble()
        self.assertTrue((self.app / "hesta" / "factory.py").is_file())
        self.assertTrue((self.app / "sample_emails").is_dir())
        self.assertIn("first client's config", (self.app / "config.yaml").read_text(encoding="utf-8"))

    def test_the_build_says_where_the_pipeline_came_from(self):
        info = self.assemble()
        readme = (Path(info["out"]) / "README-BUILD.md").read_text(encoding="utf-8")
        self.assertIn(f"`{PACKAGE}` package", readme)
        self.assertNotIn("no pipeline overlay", readme)

    def test_no_overlay_keeps_the_whole_app_and_warns(self):
        self.overlay_path.unlink()
        info = self.assemble()
        out_app = Path(info["out"]) / APP_REL
        self.assertTrue((out_app / "hesta" / "factory.py").is_file())
        self.assertFalse(info["pipeline_overlay"])
        self.assertIn("no pipeline overlay", (Path(info["out"]) / "README-BUILD.md").read_text(encoding="utf-8"))
        self.assertIn("first client's config", (out_app / "config.yaml").read_text(encoding="utf-8"))

    def test_an_invalid_overlay_stops_the_build_before_anything_is_written(self):
        _write_overlay(self.overlay_path, _overlay(package="not_written_yet"))
        with self.assertRaisesRegex(assemble_client.AssembleError, "does not exist: write the client package first"):
            self.assemble()
        self.assertFalse((self.tmp / "out").exists())

    def test_a_named_overlay_that_is_not_there_is_refused(self):
        with self.assertRaisesRegex(assemble_client.AssembleError, "does not exist"):
            self.assemble(pipeline_file=self.tmp / "typo.yaml")

    def test_the_merged_config_passes_the_real_schema(self):
        try:
            from strands_compose import load_config
        except ImportError as exc:
            self.skipTest(f"needs strands-compose ({exc})")
        out_app = Path(self.assemble()["out"]) / APP_REL
        config = load_config(str(out_app / "config.yaml"))
        self.assertEqual(sorted(config.agents), ["coordinator", "intake"])


if __name__ == "__main__":
    unittest.main()
