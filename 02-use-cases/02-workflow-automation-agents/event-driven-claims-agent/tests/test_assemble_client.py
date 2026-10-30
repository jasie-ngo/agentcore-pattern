"""scripts/assemble_client.py: a deployable project per client (ADR-0015, Q7).  Offline: nothing is deployed.

Unit level (always runs): the client's names are derived and distinct, the base environment's identity never leaks into a client's
project, a pack only ships switched on if it verifies, and the tool refuses to overwrite anything it did not make.

Integration level (needs Node and `npm ci` in agentcore/cdk): two assembled clients are synthesised into ONE account and region,
and NO physical name is shared, including the names the AgentCore construct derives from the project name.

Run:
    python3 -m unittest tests.test_assemble_client -v
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
CDK = ROOT / "agentcore" / "cdk"

_spec = importlib.util.spec_from_file_location("assemble_client", ROOT / "scripts" / "assemble_client.py")
assemble_client = importlib.util.module_from_spec(_spec)
sys.modules["assemble_client"] = assemble_client
_spec.loader.exec_module(assemble_client)

BASE = json.loads((ROOT / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
BASE_POOL = "ap-southeast-2_xMeKwrxW6"  # the base project's own Cognito identity: it must never reach a client's project
BASE_CLIENT_ID = "41ppu6uq1or79jkgrkag8f4rbj"

GOOD_SLUGS = ["acme", "hesta", "fund-a1", "abc", "a" * 24]
BAD_SLUGS = ["a", "ab", "Acme", "acme_corp", "-acme", "acme-", "ac--me", "1acme", "a" * 25, "ac me", "claimsagent", "aws", ""]


def _pack_loader_ready() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


class SlugTests(unittest.TestCase):
    def test_good_slugs_are_accepted(self):
        for slug in GOOD_SLUGS:
            with self.subTest(slug=slug):
                self.assertEqual(assemble_client.parse_slug(slug), slug)

    def test_bad_slugs_are_refused_with_a_readable_message(self):
        for slug in BAD_SLUGS:
            with self.subTest(slug=slug), self.assertRaisesRegex(assemble_client.AssembleError, "Invalid client"):
                assemble_client.parse_slug(slug)

    def test_the_python_rules_agree_with_the_typescript_rules(self):
        # Two implementations of one rule: they must not drift. Compared over the same lists, when Node and a build exist.
        compiled = CDK / "dist" / "lib" / "naming.js"
        if shutil.which("node") is None or not compiled.is_file():
            self.skipTest("needs Node and a built agentcore/cdk (run `npm test` there)")
        script = (
            "const {parseClient}=require(process.argv[1]);"
            "const ok=s=>{try{return parseClient(s)!==undefined}catch(e){return false}};"
            "console.log(JSON.stringify(JSON.parse(process.argv[2]).map(ok)))"
        )
        slugs = GOOD_SLUGS + [s for s in BAD_SLUGS if s]
        result = subprocess.run(["node", "-e", script, str(compiled), json.dumps(slugs)], capture_output=True, text=True, check=True)
        typescript = json.loads(result.stdout)
        python = []
        for slug in slugs:
            try:
                assemble_client.parse_slug(slug)
                python.append(True)
            except assemble_client.AssembleError:
                python.append(False)
        self.assertEqual(python, typescript, dict(zip(slugs, zip(python, typescript))))

    def test_project_names_are_letters_and_digits_only(self):
        self.assertEqual(assemble_client.project_name("acme"), "Acme")
        self.assertEqual(assemble_client.project_name("fund-a1"), "FundA1")
        self.assertNotEqual(assemble_client.project_name("a-bc"), assemble_client.project_name("ab-c"))


def _render(slug="acme", **kwargs):
    defaults = dict(profile="strict", discovery_url=None, client_id=None, enable_pack=False)
    defaults.update(kwargs)
    return assemble_client.render_spec(BASE, slug, **defaults)


def _env(spec):
    return {e["name"]: e["value"] for e in spec["runtimes"][0]["envVars"]}


class RenderSpecTests(unittest.TestCase):
    def test_the_clients_own_names_replace_the_base_names(self):
        spec = _render("fund-a1")
        self.assertEqual(spec["name"], "FundA1")
        self.assertEqual(spec["memories"][0]["name"], "FundA1Memory")
        self.assertEqual(spec["credentials"][0]["name"], "fund-a1-gateway-m2m")
        self.assertEqual(_env(spec)["AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER"], "fund-a1-gateway-m2m")

    def test_the_base_environments_identity_never_reaches_a_client(self):
        raw = json.dumps(_render("acme"))
        self.assertNotIn(BASE_POOL, raw)
        self.assertNotIn(BASE_CLIENT_ID, raw)
        self.assertNotIn("cognito-gateway-m2m", raw)

    def test_the_identity_is_a_well_formed_placeholder_until_the_client_supplies_its_own(self):
        spec = _render("acme")
        url = spec["credentials"][0]["discoveryUrl"]
        self.assertTrue(url.endswith("/.well-known/openid-configuration"))  # `agentcore validate` insists on the shape
        self.assertIn("PLACEHOLDER", url)
        jwt = spec["agentCoreGateways"][0]["authorizerConfiguration"]["customJwtAuthorizer"]
        self.assertEqual(jwt["allowedClients"], ["PLACEHOLDER_CLIENT_ID"])
        own = _render("acme", discovery_url="https://idp.example/acme/.well-known/openid-configuration", client_id="abc123")
        self.assertEqual(own["agentCoreGateways"][0]["authorizerConfiguration"]["customJwtAuthorizer"]["allowedClients"], ["abc123"])

    def test_the_profile_is_set_and_the_pack_is_off_unless_asked_for(self):
        self.assertEqual(_env(_render())["DEPLOYMENT_PROFILE"], "strict")
        self.assertEqual(_env(_render(profile="presale"))["DEPLOYMENT_PROFILE"], "presale")
        self.assertNotIn("CEDAR_PACK_DIR", _env(_render()))
        self.assertEqual(_env(_render(enable_pack=True))["CEDAR_PACK_DIR"], "/app/client_pack")

    def test_the_base_spec_is_not_modified(self):
        before = json.dumps(BASE, sort_keys=True)
        _render("acme")
        self.assertEqual(json.dumps(BASE, sort_keys=True), before)

    def test_two_clients_share_no_name_bearing_field(self):
        a, b = _render("acme"), _render("globex")
        fields = lambda s: {s["name"], s["memories"][0]["name"], s["credentials"][0]["name"]}
        self.assertEqual(fields(a) & fields(b), set())

    def test_a_leak_of_the_base_credential_name_is_caught(self):
        leaky = json.loads(json.dumps(BASE))
        leaky["runtimes"][0]["envVars"].append({"name": "SOMETHING_ELSE", "value": "cognito-gateway-m2m"})
        with self.assertRaisesRegex(assemble_client.AssembleError, "leak"):
            assemble_client.render_spec(leaky, "acme", profile="strict", discovery_url=None, client_id=None, enable_pack=False)


class AssembleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def assemble(self, slug="acme", name="out", **kwargs):
        kwargs.setdefault("pack_dir", self.tmp / "no-pack")  # keep the repo's own client folders out of these tests
        return assemble_client.assemble(slug, self.tmp / name, **kwargs)

    def test_the_layout_is_complete_and_clean(self):
        info = self.assemble(display_name="Acme Super", profile="presale")
        out = Path(info["out"])
        for rel in ("agentcore/agentcore.json", "agentcore/cdk/lib/naming.ts", "agentcore/cdk/package.json", "lambdas/schemas",
                    "app/04_hesta_member_email/Dockerfile", "app/04_hesta_member_email/uv.lock", "app/04_hesta_member_email/core/cedar_pack.py",
                    "README-BUILD.md", "BUILD-INFO.json", assemble_client.MARKER):
            self.assertTrue((out / rel).exists(), rel)
        for unwanted in ("agentcore/cdk/node_modules", "agentcore/cdk/dist", "agentcore/cdk/cdk.out", "agentcore/aws-targets.json"):
            self.assertFalse((out / unwanted).exists(), unwanted)
        self.assertEqual([p for p in out.rglob("__pycache__")], [])

    def test_the_cdk_is_pointed_at_the_client(self):
        out = Path(self.assemble(display_name="Acme Super")["out"])
        context = json.loads((out / "agentcore" / "cdk" / "cdk.json").read_text())["context"]
        self.assertEqual((context["client"], context["clientName"]), ("acme", "Acme Super"))

    def test_the_base_project_is_left_alone(self):
        before = (ROOT / "agentcore" / "agentcore.json").read_bytes()
        self.assemble()
        self.assertEqual((ROOT / "agentcore" / "agentcore.json").read_bytes(), before)

    def test_it_will_not_overwrite_a_directory(self):
        self.assemble()
        with self.assertRaisesRegex(assemble_client.AssembleError, "already exists"):
            self.assemble()
        stranger = self.tmp / "stranger"
        stranger.mkdir()
        (stranger / "precious.txt").write_text("not made by the tool")
        with self.assertRaisesRegex(assemble_client.AssembleError, "not made by this tool"):
            assemble_client.assemble("acme", stranger, force=True, pack_dir=self.tmp / "no-pack")
        self.assertTrue((stranger / "precious.txt").exists())

    def test_it_will_rebuild_what_it_made_with_force(self):
        out = Path(self.assemble()["out"])
        (out / "stale.txt").write_text("left over")
        self.assemble(force=True)
        self.assertFalse((out / "stale.txt").exists())

    def test_bad_input_is_refused_before_anything_is_written(self):
        with self.assertRaisesRegex(assemble_client.AssembleError, "Invalid client"):
            self.assemble("Acme_Corp", name="never")
        with self.assertRaisesRegex(assemble_client.AssembleError, "--profile"):
            self.assemble(profile="relaxed", name="never")
        self.assertFalse((self.tmp / "never").exists())

    def test_the_command_line_reports_an_error_and_exits_non_zero(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "assemble_client.py"), "Bad_Slug", "--out", str(self.tmp / "x")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR: Invalid client", result.stderr)

    # ---- the client's rule pack -------------------------------------------------------------------------------------
    def test_an_uncompiled_pack_is_refused(self):
        pack = self.tmp / "pack"
        pack.mkdir()
        with self.assertRaisesRegex(assemble_client.AssembleError, "no manifest.json"):
            self.assemble(pack_dir=pack)

    @unittest.skipUnless(_pack_loader_ready(), "needs PyYAML")
    def test_a_pack_is_shipped_but_off_by_default(self):
        info = self.assemble(pack_dir=ROOT / "clients" / "hesta" / "pack")
        out = Path(info["out"])
        self.assertTrue((out / "app/04_hesta_member_email/client_pack/manifest.json").is_file())
        self.assertTrue((info["pack_included"], info["pack_enabled"]) == (True, False))
        spec = json.loads((out / "agentcore" / "agentcore.json").read_text())
        self.assertNotIn("CEDAR_PACK_DIR", {e["name"] for e in spec["runtimes"][0]["envVars"]})

    @unittest.skipUnless(_pack_loader_ready(), "needs PyYAML")
    def test_an_unapproved_pack_can_not_be_switched_on(self):
        with self.assertRaisesRegex(assemble_client.AssembleError, "agent would not start"):
            self.assemble(pack_dir=ROOT / "clients" / "hesta" / "pack", enable_pack=True)

    @unittest.skipUnless(_pack_loader_ready(), "needs PyYAML")
    def test_an_approved_pack_can_be_switched_on(self):
        sys.path.insert(0, str(APP))
        from core import cedar_pack  # noqa: PLC0415

        approved = cedar_pack._fresh_pack(str(self.tmp), "approved", ROOT / "rules" / "packs" / "health-insurer-illustrative")
        info = self.assemble(pack_dir=approved, enable_pack=True)
        spec = json.loads((Path(info["out"]) / "agentcore" / "agentcore.json").read_text())
        self.assertEqual({e["name"]: e["value"] for e in spec["runtimes"][0]["envVars"]}["CEDAR_PACK_DIR"], "/app/client_pack")

    # ---- the client's guardrail wording -------------------------------------------------------------------------------
    def _guardrail_file(self, body='{"version": 1}') -> Path:
        path = self.tmp / "topics.json"
        path.write_text(body, encoding="utf-8")
        return path

    def test_a_clients_guardrail_file_is_copied_where_the_cdk_app_looks(self):
        source = self._guardrail_file('{"version": 1, "marker": "from the client"}')
        info = self.assemble(guardrail_file=source)
        copied = Path(info["out"]) / "agentcore" / "guardrail.json"
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        self.assertTrue(info["guardrail_included"])
        self.assertIn("this client's own file", (Path(info["out"]) / "README-BUILD.md").read_text())

    def test_with_no_guardrail_file_nothing_is_copied_and_the_build_says_so(self):
        info = self.assemble()
        self.assertFalse((Path(info["out"]) / "agentcore" / "guardrail.json").exists())
        self.assertFalse(info["guardrail_included"])
        self.assertIn("neutral default wording", (Path(info["out"]) / "README-BUILD.md").read_text())

    def test_the_conventional_location_is_found_from_the_client_name(self):
        folder = self.tmp / "clients" / "acme" / "guardrail"
        folder.mkdir(parents=True)
        (folder / "topics.json").write_text('{"version": 1}', encoding="utf-8")
        info = self.assemble(root=self._shadow_root())
        self.assertTrue(info["guardrail_included"])

    def _shadow_root(self) -> Path:
        """A root whose clients/ folder is this test's own, with just enough of the real project beside it to assemble from."""
        shadow = self.tmp
        for rel in ("agentcore", "lambdas", "app"):
            if not (shadow / rel).exists():
                (shadow / rel).symlink_to(ROOT / rel, target_is_directory=True)
        return shadow

    def test_a_named_guardrail_file_that_is_not_there_is_refused(self):
        with self.assertRaisesRegex(assemble_client.AssembleError, "does not exist"):
            self.assemble(guardrail_file=self.tmp / "typo.json")

    def test_a_guardrail_file_that_is_not_a_json_object_is_refused(self):
        with self.assertRaisesRegex(assemble_client.AssembleError, "not valid JSON"):
            self.assemble(guardrail_file=self._guardrail_file("{ nope"))
        with self.assertRaisesRegex(assemble_client.AssembleError, "one JSON object"):
            self.assemble(name="out2", guardrail_file=self._guardrail_file("[1, 2]"))

    def test_switching_on_a_pack_that_is_not_there_is_refused(self):
        with self.assertRaisesRegex(assemble_client.AssembleError, "no pack"):
            self.assemble(enable_pack=True)


SKIP_REASON = ""
if shutil.which("node") is None or shutil.which("npx") is None:
    SKIP_REASON = "needs Node.js"
elif not (CDK / "node_modules").is_dir():
    SKIP_REASON = "needs the CDK dependencies: run `npm ci` in agentcore/cdk"

ACCOUNT, REGION = "111122223333", "ap-southeast-2"
# Resource types whose "Name" is a plain physical name that must be unique in an account and region.
STRUCTURAL = {
    "AWS::IAM::Role", "AWS::IAM::Policy", "AWS::Lambda::Permission", "AWS::Lambda::EventInvokeConfig", "AWS::S3::BucketPolicy",
    "Custom::S3AutoDeleteObjects", "Custom::S3BucketNotifications", "AWS::CloudFormation::CustomResource",
    "AWS::BedrockAgentCore::GatewayTarget", "AWS::BedrockAgentCore::Policy",
}


def _physical_names(template: dict) -> dict:
    out = {}
    for resource in template["Resources"].values():
        if resource["Type"] in STRUCTURAL:
            continue
        for key, value in resource.get("Properties", {}).items():
            if isinstance(value, str) and (key.endswith("Name") or key == "Name") and key != "MetricName":
                out[f"{resource['Type'].split('::')[-1]}.{key}"] = value
    return out


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class TwoAssembledClientsTests(unittest.TestCase):
    """The claim that matters: two clients in one account and region share no physical name."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.dirs, cls.templates = {}, {}
        for slug, display, profile in (("acme", "Acme Super", "presale"), ("globex", None, "strict")):
            info = assemble_client.assemble(slug, base / slug, display_name=display, profile=profile, link_node_modules=True,
                                            pack_dir=base / "no-pack")
            out = Path(info["out"])
            (out / "agentcore" / "aws-targets.json").write_text(json.dumps([{"name": "dev", "account": ACCOUNT, "region": REGION}]))
            built = subprocess.run(["npx", "tsc"], cwd=out / "agentcore" / "cdk", capture_output=True, text=True)
            if built.returncode != 0:
                raise RuntimeError(f"{slug}: the CDK project does not compile:\n{built.stdout[-1500:]}")
            env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTCORE_CLIENT")}
            env.update(AWS_REGION=REGION, CDK_OUTDIR=str(out / "cdk.out"))
            run = subprocess.run(["node", "dist/bin/cdk.js"], cwd=out / "agentcore" / "cdk", env=env, capture_output=True, text=True)
            if run.returncode != 0:
                raise RuntimeError(f"{slug}: synth failed:\n{run.stderr[-1500:]}")
            stack = f"AgentCore-{assemble_client.project_name(slug)}-dev"
            cls.templates[slug] = json.loads((out / "cdk.out" / f"{stack}.template.json").read_text())
            cls.dirs[slug] = out

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_no_physical_name_is_shared(self):
        a, b = _physical_names(self.templates["acme"]), _physical_names(self.templates["globex"])
        self.assertGreater(len(a), 15)  # the check sees the infra's names AND the AgentCore construct's
        self.assertEqual({k: v for k, v in a.items() if b.get(k) == v}, {})

    def test_each_client_is_really_in_client_mode(self):
        for slug, template in self.templates.items():
            with self.subTest(client=slug):
                names = _physical_names(template)
                self.assertTrue(any(n.startswith(f"{slug}-inbox-") for n in names.values()), names)
                keys = [r for r in template["Resources"].values()
                        if r["Type"] == "AWS::KMS::Key" and "Data key for client" in r["Properties"].get("Description", "")]
                self.assertEqual(len(keys), 1)

    def test_each_runtime_has_its_own_profile_credential_and_memory_id(self):
        for slug, profile in (("acme", "presale"), ("globex", "strict")):
            with self.subTest(client=slug):
                runtime = next(r for r in self.templates[slug]["Resources"].values() if r["Type"] == "AWS::BedrockAgentCore::Runtime")
                env = runtime["Properties"]["EnvironmentVariables"]
                self.assertEqual(env["DEPLOYMENT_PROFILE"], profile)
                self.assertEqual(env["AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER"], f"{slug}-gateway-m2m")
                self.assertIn("AGENTCORE_MEMORY_ID", env)

    def test_the_guardrail_speaks_for_each_client(self):
        said = {s: next(r for r in t["Resources"].values() if r["Type"] == "AWS::Bedrock::Guardrail")["Properties"]["BlockedInputMessaging"]
                for s, t in self.templates.items()}
        self.assertIn("Acme Super", said["acme"])
        self.assertNotIn("HESTA", said["globex"])

    @unittest.skipIf(shutil.which("agentcore") is None, "needs the AgentCore CLI")
    def test_each_assembled_project_validates(self):
        for slug, out in self.dirs.items():
            with self.subTest(client=slug):
                result = subprocess.run(["agentcore", "validate"], cwd=out, capture_output=True, text=True)
                self.assertIn("Valid", result.stdout + result.stderr, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
