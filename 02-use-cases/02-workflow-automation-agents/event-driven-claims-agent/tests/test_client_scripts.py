"""The deploy, teardown, Cognito, seed and test scripts act on ONE deployment: the original, or one client's.  ADR-0015, Q7.

Offline: nothing here contacts AWS.  The shell scripts are run with `--plan` and an `aws` on the PATH that fails if it is ever
called, which proves they print their names and stop before any AWS call.

What this protects:
  - the original deployment keeps its original names exactly (a regression here would make an existing stack unreachable);
  - a client's names come from its own assembled project, and two clients share none;
  - a script refuses a project that belongs to another client, instead of acting on it;
  - the one dangerous lookup, matching leftover AgentCore resources by name, cannot reach another client's resources;
  - no script still hard-codes the original names outside the places that are meant to.

Run:
    python3 -m unittest tests.test_client_scripts -v
"""

import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import assemble_client  # noqa: E402
import clientctx  # noqa: E402

BASH = shutil.which("bash")

LEGACY = {
    "stack": "AgentCore-ClaimsAgent-dev",
    "credential": "cognito-gateway-m2m",
    "pool": "ClaimsAgent-UserPool",
    "app_client": "ClaimsAgent-M2M",
    "policies": "ClaimsAgent-dev-Policies",
    "claims": "ClaimsAgent-dev-Claims",
}
GOOD_URL = "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_EXAMPLE1/.well-known/openid-configuration"
GOOD_CLIENT_ID = "exampleclient123"


def _assemble(slug: str, out: Path, **kwargs) -> Path:
    assemble_client.assemble(slug, out, profile="presale", **kwargs)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clientctx-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.acme = _assemble("acme", self.tmp / "acme")
        self.corp = _assemble("acme-corp", self.tmp / "acme-corp")


class OriginalNamesAreUnchanged(Base):
    def test_no_client_gives_the_original_names_exactly(self):
        ctx = clientctx.resolve(env={})
        self.assertEqual(ctx.mode, "legacy")
        self.assertEqual(ctx.stack_name, LEGACY["stack"])
        self.assertEqual(ctx.credential, LEGACY["credential"])
        self.assertEqual(ctx.cognito_pool_name, LEGACY["pool"])
        self.assertEqual(ctx.cognito_app_client_name, LEGACY["app_client"])
        self.assertEqual(ctx.table("Policies"), LEGACY["policies"])
        self.assertEqual(ctx.table("Claims"), LEGACY["claims"])
        self.assertEqual(ctx.domain_prefix("123456789012"), "claims-agent-123456789012")
        self.assertEqual(ctx.bucket("123456789012", "us-west-2"), "claims-inbox-123456789012-us-west-2")
        self.assertEqual(ctx.function("Trigger"), "ClaimsAgent-Trigger")
        self.assertEqual(ctx.project_dir, ROOT)

    def test_the_legacy_stack_name_still_matches_what_the_cdk_app_builds(self):
        # bin/cdk.ts: AgentCore-<project>-<target>, underscores to hyphens, from the base agentcore.json's own name.
        base = json.loads((ROOT / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
        self.assertEqual(clientctx.resolve(env={}).stack_name, f"AgentCore-{base['name'].replace('_', '-')}-dev")

    def test_the_environment_can_still_override_the_credential_without_a_client(self):
        self.assertEqual(clientctx.resolve(env={}).checked_credential("my-provider"), "my-provider")

    def test_owns_resource_keeps_the_original_loose_match_without_a_client(self):
        ctx = clientctx.resolve(env={})
        for name in ["ClaimsGateway", "ClaimsAgentMemory", "claimsagent-runtime", "claims-policy-engine"]:
            self.assertTrue(clientctx.owns_resource(name, ctx), name)
        self.assertFalse(clientctx.owns_resource("OtherThing", ctx))


class ClientNames(Base):
    def test_a_client_is_named_from_its_own_project(self):
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        self.assertEqual(ctx.mode, "client")
        self.assertEqual(ctx.project, "Acme")
        self.assertEqual(ctx.stack_name, "AgentCore-Acme-dev")
        self.assertEqual(ctx.credential, "acme-gateway-m2m")
        self.assertEqual(ctx.cognito_pool_name, "Acme-UserPool")
        self.assertEqual(ctx.cognito_app_client_name, "Acme-M2M")
        self.assertEqual(ctx.domain_prefix("123456789012"), "acme-auth-123456789012")
        # naming.ts: <client>-<stage>-<Kind> for tables, <client>-inbox-<account>-<region> for the bucket, <client>-<Kind> for functions.
        self.assertEqual(ctx.table("Policies"), "acme-dev-Policies")
        self.assertEqual(ctx.bucket("123456789012", "ap-southeast-2"), "acme-inbox-123456789012-ap-southeast-2")
        self.assertEqual(ctx.function("Trigger"), "acme-Trigger")
        self.assertEqual(ctx.env_file, self.acme.resolve() / ".env")
        self.assertEqual(ctx.cognito_state_file, self.acme.resolve() / ".cognito-state.json")

    def test_a_hyphenated_client_gets_a_camel_case_project_and_still_its_own_names(self):
        ctx = clientctx.resolve(client="acme-corp", project_dir=self.corp, env={})
        self.assertEqual(ctx.project, "AcmeCorp")
        self.assertEqual(ctx.stack_name, "AgentCore-AcmeCorp-dev")
        self.assertEqual(ctx.table("Claims"), "acme-corp-dev-Claims")

    def test_two_clients_share_no_name_the_scripts_derive(self):
        a = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        b = clientctx.resolve(client="acme-corp", project_dir=self.corp, env={})
        legacy = clientctx.resolve(env={})

        def names(ctx):
            return {
                ctx.stack_name,
                ctx.credential,
                ctx.cognito_pool_name,
                ctx.cognito_app_client_name,
                ctx.domain_prefix("123456789012"),
                ctx.table("Policies"),
                ctx.table("Claims"),
                ctx.table("Reviews"),
                ctx.bucket("123456789012", "ap-southeast-2"),
                ctx.function("Trigger"),
                str(ctx.env_file),
                str(ctx.cognito_state_file),
            }

        for x, y in [(a, b), (a, legacy), (b, legacy)]:
            self.assertEqual(names(x) & names(y), set(), f"{x.mode}:{x.client} and {y.mode}:{y.client} share a name")

    def test_the_client_comes_from_the_environment_when_no_flag_is_given(self):
        ctx = clientctx.resolve(env={"AGENTCORE_CLIENT": "acme", "AGENTCORE_PROJECT_DIR": str(self.acme)})
        self.assertEqual(ctx.client, "acme")

    def test_a_target_changes_the_stack_and_tables(self):
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, target="staging", env={})
        self.assertEqual(ctx.stack_name, "AgentCore-Acme-staging")
        self.assertEqual(ctx.table("Policies"), "acme-staging-Policies")


class RefusesTheWrongProject(Base):
    def test_a_directory_that_belongs_to_another_client_is_refused(self):
        with self.assertRaisesRegex(clientctx.CtxError, 'belongs to client "acme", not "acme-corp"'):
            clientctx.resolve(client="acme-corp", project_dir=self.acme, env={})

    def test_a_missing_project_says_how_to_assemble_it(self):
        with self.assertRaisesRegex(clientctx.CtxError, "assemble_client.py nobody"):
            clientctx.resolve(client="nobody", project_dir=self.tmp / "nobody", env={})

    def test_a_directory_the_assembler_did_not_make_is_refused(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        with self.assertRaisesRegex(clientctx.CtxError, "was not made by scripts/assemble_client.py"):
            clientctx.resolve(client="acme", project_dir=plain, env={})

    def test_a_project_renamed_behind_the_scripts_back_is_refused(self):
        path = self.acme / "agentcore" / "agentcore.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["name"] = "SomethingElse"
        path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(clientctx.CtxError, 'expected "Acme"'):
            clientctx.resolve(client="acme", project_dir=self.acme, env={})

    def test_a_bad_slug_is_refused_with_the_assemblers_message(self):
        for slug in ["Acme", "a", "ac--me", "claimsagent", "../acme"]:
            with self.subTest(slug=slug), self.assertRaisesRegex(clientctx.CtxError, "Invalid client"):
                clientctx.resolve(client=slug, env={})

    def test_a_credential_in_the_environment_that_is_not_the_clients_is_refused(self):
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        with self.assertRaisesRegex(clientctx.CtxError, "comes from its own agentcore.json"):
            ctx.checked_credential("cognito-gateway-m2m")
        self.assertEqual(ctx.checked_credential(None), "acme-gateway-m2m")
        self.assertEqual(ctx.checked_credential("acme-gateway-m2m"), "acme-gateway-m2m")

    def test_a_bad_target_is_refused(self):
        with self.assertRaisesRegex(clientctx.CtxError, "Invalid target"):
            clientctx.resolve(target="dev; rm -rf /", env={})


class ApplyIdentity(Base):
    def _spec(self, project: Path) -> dict:
        return json.loads((project / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))

    def test_the_clients_own_identity_replaces_the_placeholders_and_nothing_else_changes(self):
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        before = self._spec(self.acme)
        clientctx.apply_identity(ctx, GOOD_URL, GOOD_CLIENT_ID)
        after = self._spec(self.acme)
        self.assertEqual(after["credentials"][0]["discoveryUrl"], GOOD_URL)
        for gateway in after["agentCoreGateways"]:
            jwt = gateway["authorizerConfiguration"]["customJwtAuthorizer"]
            self.assertEqual(jwt["discoveryUrl"], GOOD_URL)
            self.assertEqual(jwt["allowedClients"], [GOOD_CLIENT_ID])
        # Everything that is not the identity is untouched.
        after["credentials"][0]["discoveryUrl"] = before["credentials"][0]["discoveryUrl"]
        for b, a in zip(before["agentCoreGateways"], after["agentCoreGateways"]):
            a["authorizerConfiguration"]["customJwtAuthorizer"] = b["authorizerConfiguration"]["customJwtAuthorizer"]
        self.assertEqual(before, after)

    def test_another_clients_project_is_not_touched(self):
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        other_before = (self.corp / "agentcore" / "agentcore.json").read_text(encoding="utf-8")
        clientctx.apply_identity(ctx, GOOD_URL, GOOD_CLIENT_ID)
        self.assertEqual((self.corp / "agentcore" / "agentcore.json").read_text(encoding="utf-8"), other_before)

    def test_the_result_is_valid_json_and_no_temporary_file_is_left(self):
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        clientctx.apply_identity(ctx, GOOD_URL, GOOD_CLIENT_ID)
        json.loads((self.acme / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in (self.acme / "agentcore").glob(".agentcore-*")], [])

    def test_a_bad_url_or_client_id_is_refused_and_nothing_is_written(self):
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        before = (self.acme / "agentcore" / "agentcore.json").read_text(encoding="utf-8")
        for url, cid in [
            ("http://evil.example/.well-known/openid-configuration", GOOD_CLIENT_ID),
            ("https://cognito-idp.ap-southeast-2.amazonaws.com/pool", GOOD_CLIENT_ID),
            (GOOD_URL, "short"),
            (GOOD_URL, "has spaces in it!!"),
            (GOOD_URL, ""),
        ]:
            with self.subTest(url=url, cid=cid), self.assertRaises(clientctx.CtxError):
                clientctx.apply_identity(ctx, url, cid)
        self.assertEqual((self.acme / "agentcore" / "agentcore.json").read_text(encoding="utf-8"), before)

    def test_the_original_projects_file_is_never_rewritten_by_a_script(self):
        with self.assertRaisesRegex(clientctx.CtxError, "only for a client"):
            clientctx.apply_identity(clientctx.resolve(env={}), GOOD_URL, GOOD_CLIENT_ID)


class OrphanMatching(Base):
    def setUp(self):
        super().setUp()
        self.ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        self.siblings = {"Acme", "AcmeCorp", "Hesta"}

    def test_a_client_matches_its_own_names_as_whole_words(self):
        for name in ["Acme", "Acme-Gateway", "Acme_Runtime", "AcmeMemory", "acme-gateway", "Acme.v2"]:
            self.assertTrue(clientctx.owns_resource(name, self.ctx, self.siblings), name)

    def test_a_client_does_not_match_a_longer_clients_names(self):
        for name in ["AcmeCorp", "AcmeCorpMemory", "AcmeCorp-Gateway"]:
            self.assertFalse(clientctx.owns_resource(name, self.ctx, self.siblings), name)

    def test_a_client_does_not_match_unrelated_or_look_alike_names(self):
        for name in ["Acmeco", "Acme2", "Hesta-Gateway", "ClaimsGateway", "ClaimsAgentMemory", "Macme", ""]:
            self.assertFalse(clientctx.owns_resource(name, self.ctx, self.siblings), name)

    def test_the_longer_client_still_matches_its_own(self):
        corp = clientctx.resolve(client="acme-corp", project_dir=self.corp, env={})
        self.assertTrue(clientctx.owns_resource("AcmeCorp-Gateway", corp, self.siblings))
        self.assertFalse(clientctx.owns_resource("Acme-Gateway", corp, self.siblings))

    def test_known_projects_reads_the_checkouts_clients_and_builds(self):
        root = self.tmp / "checkout"
        (root / "build" / "acme").mkdir(parents=True)
        (root / "clients" / "acme-corp").mkdir(parents=True)
        (root / "clients" / "_template").mkdir(parents=True)
        (root / "clients" / "Not_A_Slug").mkdir(parents=True)
        self.assertEqual(clientctx.known_projects(root), {"Acme", "AcmeCorp"})


@unittest.skipUnless(BASH, "needs bash")
class ShellScriptsStopAtPlanBeforeAnyAwsCall(Base):
    """Every script, run with --plan and a failing `aws` on the PATH: it prints the names and makes no AWS call."""

    SCRIPTS_RUN = [
        (["deploy.sh", "us-west-2"], "deploy"),
        (["destroy.sh", "us-west-2"], "destroy"),
        (["scripts/destroy.sh", "us-west-2"], "tear down"),
        (["scripts/setup_cognito.sh", "us-west-2"], "create Cognito pool"),
        (["scripts/teardown_cognito.sh"], "delete the Cognito pool"),
        (["scripts/fix_credential_region.sh", "us-east-1"], "update credential provider"),
    ]

    def setUp(self):
        super().setUp()
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        marker = self.tmp / "aws-was-called"
        fake = self.fakebin / "aws"
        fake.write_text(f'#!/bin/sh\necho called >> "{marker}"\necho "FAKE aws was called: $*" >&2\nexit 99\n', encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.marker = marker

    def _env(self):
        env = {**os.environ, "PATH": f"{self.fakebin}{os.pathsep}{os.environ['PATH']}"}
        for key in ("AGENTCORE_CLIENT", "AGENTCORE_PROJECT_DIR", "AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER"):
            env.pop(key, None)
        return env

    def _run(self, argv, *extra):
        return subprocess.run([BASH, str(ROOT / argv[0]), *argv[1:], *extra, "--plan"], cwd=ROOT, env=self._env(), capture_output=True, text=True)

    def test_the_original_deployment_prints_its_original_names(self):
        for argv, verb in self.SCRIPTS_RUN:
            with self.subTest(script=argv[0]):
                result = self._run(argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(verb, result.stdout)
                self.assertIn(LEGACY["stack"], result.stdout)
                self.assertIn(LEGACY["credential"], result.stdout)
                self.assertIn(LEGACY["pool"], result.stdout)
        self.assertFalse(self.marker.exists(), "a script called aws in --plan mode")

    def test_a_client_prints_its_own_names_and_none_of_the_originals(self):
        for argv, verb in self.SCRIPTS_RUN:
            with self.subTest(script=argv[0]):
                result = self._run(argv, "--client", "acme", "--project-dir", str(self.acme))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(verb, result.stdout)
                self.assertIn("AgentCore-Acme-dev", result.stdout)
                self.assertIn("acme-gateway-m2m", result.stdout)
                self.assertIn("Acme-UserPool", result.stdout)
                self.assertNotIn(LEGACY["stack"], result.stdout)
                self.assertNotIn(LEGACY["credential"], result.stdout)
        self.assertFalse(self.marker.exists(), "a script called aws in --plan mode")

    def test_a_client_that_was_never_assembled_stops_with_a_readable_message(self):
        for argv, _ in self.SCRIPTS_RUN:
            with self.subTest(script=argv[0]):
                result = self._run(argv, "--client", "nobody", "--project-dir", str(self.tmp / "nobody"))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("assemble_client.py nobody", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_a_script_pointed_at_the_wrong_clients_project_refuses(self):
        for argv, _ in self.SCRIPTS_RUN:
            with self.subTest(script=argv[0]):
                result = self._run(argv, "--client", "acme-corp", "--project-dir", str(self.acme))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('belongs to client "acme"', result.stderr)
        self.assertFalse(self.marker.exists())

    def test_an_unknown_option_is_an_error_not_a_region(self):
        result = self._run(["deploy.sh", "us-west-2"], "--clinet", "acme")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option --clinet", result.stderr)

    def test_an_option_missing_its_value_is_an_error(self):
        result = subprocess.run([BASH, str(ROOT / "deploy.sh"), "us-west-2", "--client"], cwd=ROOT, env=self._env(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--client needs a value", result.stderr)

    def test_a_clients_teardown_skips_the_name_matching_sweep_unless_asked(self):
        skipped = self._run(["scripts/destroy.sh", "us-west-2"], "--client", "acme", "--project-dir", str(self.acme))
        self.assertIn("Orphan sweep: SKIPPED", skipped.stdout)
        asked = self._run(["scripts/destroy.sh", "us-west-2"], "--client", "acme", "--project-dir", str(self.acme), "--sweep-orphans")
        self.assertIn("Orphan sweep: ON", asked.stdout)
        self.assertIn("Orphan sweep: ON", self._run(["scripts/destroy.sh", "us-west-2"]).stdout)

    def test_the_shell_variables_are_quoted_so_a_path_with_spaces_survives(self):
        spaced = self.tmp / "a b" / "acme"
        spaced.parent.mkdir()
        _assemble("acme", spaced)
        result = subprocess.run(
            [BASH, "-c", 'eval "$(python3 "$1" --client acme --project-dir "$2" shell)"; echo "$CTX_PROJECT_DIR|$CTX_STACK_NAME"',
             "bash", str(SCRIPTS / "clientctx.py"), str(spaced)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), f"{spaced.resolve()}|AgentCore-Acme-dev")


@unittest.skipUnless(BASH, "needs bash")
class DeployAndDestroyRunAgainstTheClientsOwnProject(Base):
    """The real flow of deploy.sh and destroy.sh (not just --plan), run against fake `aws`, `agentcore`, `uv`, `npm` and `cdk` that log
    where and with what they were called.  Proves the wiring: which directory, which names, which credential, which seed target."""

    REGION = "us-west-2"
    DISCOVERY = f"https://cognito-idp.{REGION}.amazonaws.com/{REGION}_EXAMPLE1/.well-known/openid-configuration"

    def setUp(self):
        super().setUp()
        self.log = self.tmp / "calls.log"
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        real_python = shutil.which("python3")
        line = 'echo "$(basename "$0") cwd=$PWD client=${AGENTCORE_CLIENT:-} cred=${AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER:-} args=$*" >> "%s"' % self.log
        tools = {
            # sts answers; anything else from aws would mean an unexpected AWS call, so it is logged and fails
            "aws": f'if [ "$1 $2" = "sts get-caller-identity" ]; then echo 111122223333; exit 0; fi\n{line}\nexit 99',
            "agentcore": f"{line}\nexit 0",
            "uv": f"{line}\nexit 0",
            "npm": f"{line}\nexit 0",
            "cdk": f"{line}\nexit 0",
            # the real python3 for everything except the seed script, which would talk to DynamoDB
            "python3": f'case "$1" in *seed_dynamodb.py) {line}; exit 0;; esac\nexec "{real_python}" "$@"',
        }
        for name, body in tools.items():
            path = self.fakebin / name
            path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _env(self, **extra):
        env = {**os.environ, "PATH": f"{self.fakebin}{os.pathsep}{os.environ['PATH']}"}
        for key in ("AGENTCORE_CLIENT", "AGENTCORE_PROJECT_DIR", "AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER", "AGENTCORE_GATEWAY_CLIENT_ID",
                    "AGENTCORE_GATEWAY_CLIENT_SECRET", "COGNITO_DISCOVERY_URL", "COGNITO_USER_POOL_ID"):
            env.pop(key, None)
        env.update(extra)
        return env

    def _write_env(self, project: Path, **values):
        text = "".join(f"{k}={v}\n" for k, v in values.items())
        (project / ".env").write_text(text, encoding="utf-8")

    def _calls(self) -> list:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def _deploy(self, *extra, **env):
        return subprocess.run([BASH, str(ROOT / "deploy.sh"), self.REGION, *extra], cwd=ROOT, env=self._env(**env), capture_output=True, text=True)

    def test_deploy_acts_inside_the_clients_project_with_the_clients_names(self):
        self._write_env(
            self.acme,
            AGENTCORE_GATEWAY_CLIENT_ID=GOOD_CLIENT_ID,
            AGENTCORE_GATEWAY_CLIENT_SECRET="examplesecret",
            COGNITO_DISCOVERY_URL=self.DISCOVERY,
            COGNITO_USER_POOL_ID=f"{self.REGION}_EXAMPLE1",
        )
        repo_targets = ROOT / "agentcore" / "aws-targets.json"
        repo_targets_before = repo_targets.read_bytes() if repo_targets.exists() else None
        result = self._deploy("--client", "acme", "--project-dir", str(self.acme))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = "\n".join(self._calls())
        project = str(self.acme.resolve())
        # the credential is the client's own, registered with the client's own identity
        self.assertRegex(calls, r"agentcore cwd=%s .*args=add credential --name acme-gateway-m2m --type oauth --discovery-url %s --client-id %s" % (re.escape(project), re.escape(self.DISCOVERY), GOOD_CLIENT_ID))
        # validate and deploy run inside the project, with the client and credential in the environment
        self.assertRegex(calls, r"agentcore cwd=%s client=acme cred=acme-gateway-m2m args=validate" % re.escape(project))
        self.assertRegex(calls, r"agentcore cwd=%s client=acme cred=acme-gateway-m2m args=deploy --target dev --yes" % re.escape(project))
        # dependencies are installed in the runtime's own code directory, not a hard-coded app
        self.assertIn(f"uv cwd={project}/app/04_hesta_member_email", calls)
        self.assertNotIn("claimsagent", calls)
        # the seed script is told which client's table to write to
        self.assertRegex(calls, r"python3 cwd=%s .*args=.*seed_dynamodb\.py --region %s --client acme --project-dir %s" % (re.escape(project), self.REGION, re.escape(str(self.acme))))
        # the deployment target is written inside the project, with the account the fake sts returned
        targets = json.loads((self.acme / "agentcore" / "aws-targets.json").read_text(encoding="utf-8"))
        self.assertEqual(targets, [{"name": "dev", "account": "111122223333", "region": self.REGION}])
        # the client's identity is now in its own agentcore.json, and the other client's file is not touched
        spec = json.loads((self.acme / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["credentials"][0]["discoveryUrl"], self.DISCOVERY)
        self.assertEqual(spec["agentCoreGateways"][0]["authorizerConfiguration"]["customJwtAuthorizer"]["allowedClients"], [GOOD_CLIENT_ID])
        self.assertNotIn(GOOD_CLIENT_ID, (self.corp / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
        # the repository's own deployment target and base project are not written
        self.assertEqual(repo_targets.read_bytes() if repo_targets.exists() else None, repo_targets_before)
        self.assertIn("AgentCore-Acme-dev", result.stdout)

    def test_a_client_never_inherits_another_deployments_identity_from_the_shell(self):
        # .env has no client id: the old script would have used whatever the shell exported (another deployment's pool).
        self._write_env(self.acme, COGNITO_DISCOVERY_URL=self.DISCOVERY)
        result = self._deploy("--client", "acme", "--project-dir", str(self.acme), AGENTCORE_GATEWAY_CLIENT_ID="othersclientid999")
        calls = "\n".join(self._calls())
        self.assertNotIn("othersclientid999", calls)
        self.assertNotIn("add credential", calls)
        self.assertNotEqual(result.returncode, 0)

    def test_a_credential_name_exported_for_another_deployment_is_refused(self):
        self._write_env(self.acme, AGENTCORE_GATEWAY_CLIENT_ID=GOOD_CLIENT_ID, AGENTCORE_GATEWAY_CLIENT_SECRET="examplesecret", COGNITO_DISCOVERY_URL=self.DISCOVERY)
        result = self._deploy("--client", "acme", "--project-dir", str(self.acme), AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER="cognito-gateway-m2m")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("comes from its own agentcore.json", result.stderr)
        self.assertNotIn("add credential", "\n".join(self._calls()))

    def test_destroy_destroys_only_the_clients_stack_from_inside_its_project(self):
        result = subprocess.run([BASH, str(ROOT / "destroy.sh"), self.REGION, "--client", "acme", "--project-dir", str(self.acme)], cwd=ROOT, env=self._env(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = "\n".join(self._calls())
        self.assertRegex(calls, r"cdk cwd=%s/agentcore/cdk client=acme .*args=destroy --all --force" % re.escape(str(self.acme.resolve())))
        self.assertIn("AgentCore-Acme-dev", result.stdout)
        self.assertNotIn("AgentCore-ClaimsAgent-dev", result.stdout)
        self.assertTrue((self.acme / "agentcore" / "aws-targets.json").exists())
        self.assertFalse((self.corp / "agentcore" / "aws-targets.json").exists())


class PythonScriptsUseTheContext(Base):
    def _import(self, name):
        sys.modules.pop(name, None)
        try:
            return importlib.import_module(name)
        except ImportError as exc:  # boto3 is not needed for the rest of the suite
            self.skipTest(f"{name} needs {exc.name}")

    def test_cleanup_matches_by_the_clients_whole_name_after_configure(self):
        cleanup = self._import("cleanup_agentcore")
        self.addCleanup(cleanup.configure, clientctx.resolve(env={}))
        cleanup.configure(clientctx.resolve(client="acme", project_dir=self.acme, env={}))
        self.assertEqual(cleanup.STACK_NAME, "AgentCore-Acme-dev")
        self.assertEqual(cleanup.CREDENTIAL_NAME, "acme-gateway-m2m")
        self.assertTrue(cleanup.matches_project("Acme-Gateway"))
        self.assertFalse(cleanup.matches_project("ClaimsGateway"))
        cleanup.configure(clientctx.resolve(env={}))
        self.assertEqual(cleanup.STACK_NAME, LEGACY["stack"])
        self.assertTrue(cleanup.matches_project("ClaimsGateway"))

    def test_a_clients_seed_never_falls_back_to_scanning_for_any_policies_table(self):
        seed = self._import("seed_dynamodb")
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        client = mock.MagicMock()
        client.list_stack_resources.side_effect = RuntimeError("no stack")
        client.list_tables.side_effect = AssertionError("a client's seed must not scan all tables")
        with mock.patch.object(seed.boto3, "client", return_value=client):
            self.assertEqual(seed.get_table_name("ap-southeast-2", ctx=ctx), "acme-dev-Policies")
        client.list_stack_resources.assert_called_once_with(StackName="AgentCore-Acme-dev")

    def test_the_original_seed_still_scans_as_it_always_did(self):
        seed = self._import("seed_dynamodb")
        client = mock.MagicMock()
        client.list_stack_resources.side_effect = RuntimeError("no stack")
        client.list_tables.return_value = {"TableNames": ["Other", "SomethingPolicies-claims"]}
        with mock.patch.object(seed.boto3, "client", return_value=client):
            self.assertEqual(seed.get_table_name("us-west-2"), "ClaimsAgent-Policies")

    def test_the_hesta_seed_uses_the_clients_stack_and_exact_table(self):
        seed = self._import("seed_hesta_members")
        ctx = clientctx.resolve(client="acme", project_dir=self.acme, env={})
        client = mock.MagicMock()
        client.list_stack_resources.side_effect = RuntimeError("no stack")
        client.list_tables.side_effect = AssertionError("a client's seed must not scan all tables")
        fake_boto3 = mock.MagicMock()
        fake_boto3.client.return_value = client
        with mock.patch.dict(sys.modules, {"boto3": fake_boto3}):
            self.assertEqual(seed.get_table_name("ap-southeast-2", ctx=ctx), "acme-dev-Policies")


class NothingStillHardCodesTheOriginalNames(unittest.TestCase):
    """The original names may appear only where they are the documented default for the original deployment."""

    # file -> how many lines may still contain the original stack name (each is a default constant or a documented example).
    ALLOWED_STACK_LITERALS = {
        "scripts/cleanup_agentcore.py": 1,
        "scripts/enable_observability.py": 2,  # the default constant and the usage example in its docstring
        "scripts/disable_observability.py": 1,
        "scripts/seed_dynamodb.py": 1,
        "scripts/seed_hesta_members.py": 1,
        "scripts/test_invoke.py": 1,
        "scripts/test_cedar.py": 1,
        "scripts/test_auth.py": 1,
        "scripts/test_e2e.py": 1,
    }

    def _files(self):
        for pattern in ("deploy.sh", "destroy.sh", "scripts/*.sh", "scripts/*.py", "scripts/lib/*.sh"):
            yield from ROOT.glob(pattern)

    def test_the_original_stack_name_is_only_a_default_constant(self):
        for path in self._files():
            rel = str(path.relative_to(ROOT))
            count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if "AgentCore-ClaimsAgent-dev" in line)
            if count:
                self.assertIn(rel, self.ALLOWED_STACK_LITERALS, f"{rel} hard-codes the original stack name")
                self.assertLessEqual(count, self.ALLOWED_STACK_LITERALS[rel], f"{rel} has more hard-coded stack names than its default")

    def test_the_shell_scripts_hard_code_no_original_names_at_all(self):
        pattern = re.compile(r"ClaimsAgent-|claims-agent-|claims-inbox|cognito-gateway-m2m|app/claimsagent")
        for rel in ["deploy.sh", "destroy.sh", "scripts/destroy.sh", "scripts/setup_cognito.sh", "scripts/teardown_cognito.sh",
                    "scripts/fix_credential_region.sh", "scripts/lib/ctx.sh"]:
            for number, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                self.assertIsNone(pattern.search(line), f"{rel}:{number} hard-codes an original name: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
