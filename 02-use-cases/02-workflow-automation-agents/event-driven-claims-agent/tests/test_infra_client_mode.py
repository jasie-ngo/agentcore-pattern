"""The CDK stack in legacy mode and in client mode (ADR-0015, Q7). Offline: it synthesises templates, deploys nothing.

What is proved, from the synthesised CloudFormation:
  * With no client, the physical names are exactly the original ones (so an existing deployment is not replaced).
  * With a client, every name the stack controls carries the client, and two clients in one account and region share none.
  * In client mode the data stores use a key of the client's own; in legacy mode there is no such key.
  * The guardrail speaks in the client's name, and keeps the original wording when there is no client.
  * Every mode publishes the generic AGENTCORE_MEMORY_ID, so a memory with any name is found.
  * The client is a stack tag; a malformed slug stops the build with a readable message.

Needs Node and the CDK project's dependencies (`npm ci` in agentcore/cdk). Skips with a reason without them.

Run:
    python3 -m unittest tests.test_infra_client_mode -v
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CDK = ROOT / "agentcore" / "cdk"
TARGETS = ROOT / "agentcore" / "aws-targets.json"
ACCOUNT, REGION = "111122223333", "ap-southeast-2"
STACK = "AgentCore-ClaimsAgent-dev"

SKIP_REASON = ""
if shutil.which("node") is None or shutil.which("npx") is None:
    SKIP_REASON = "needs Node.js"
elif not (CDK / "node_modules").is_dir():
    SKIP_REASON = "needs the CDK dependencies: run `npm ci` in agentcore/cdk"

# The original names: the backward-compatibility contract. (The SNS topic and the human-review function share a name.)
ORIGINAL_NAMES = {
    "ClaimsAgent-dev-Policies", "ClaimsAgent-dev-Claims", "ClaimsAgent-dev-Reviews",
    "ClaimsAgent-PolicyLookup", "ClaimsAgent-CreateClaim", "ClaimsAgent-HumanReview", "ClaimsAgent-Notification",
    "ClaimsAgent-ListPending", "ClaimsAgent-ResolveClaim", "ClaimsAgent-Trigger",
    "ClaimsAgent-TriggerDLQ", "ClaimsAgent-FailedClaims", "ClaimsAgent-InboxTrigger", "ClaimsAgent-dev-NoPersonalAdvice",
    f"claims-inbox-{ACCOUNT}-{REGION}",
}
PHYSICAL = [
    ("AWS::DynamoDB::Table", "TableName"), ("AWS::Lambda::Function", "FunctionName"), ("AWS::SNS::Topic", "TopicName"),
    ("AWS::SQS::Queue", "QueueName"), ("AWS::CloudWatch::Alarm", "AlarmName"), ("AWS::Events::Rule", "Name"),
    ("AWS::Bedrock::Guardrail", "Name"), ("AWS::S3::Bucket", "BucketName"),
]


def _names(template: dict) -> set:
    return {
        r["Properties"][prop]
        for r in template["Resources"].values()
        for typ, prop in PHYSICAL
        if r["Type"] == typ and isinstance(r["Properties"].get(prop), str)
    }


def _of_type(template: dict, resource_type: str) -> list:
    return [r for r in template["Resources"].values() if r["Type"] == resource_type]


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class ClientModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._made_targets = not TARGETS.exists()
        if cls._made_targets:  # the app reads its deployment target from a gitignored file
            TARGETS.write_text(json.dumps([{"name": "dev", "account": ACCOUNT, "region": REGION}]), encoding="utf-8")
        built = subprocess.run(["npx", "tsc"], cwd=CDK, capture_output=True, text=True, check=False)
        if built.returncode != 0:
            raise RuntimeError(f"the CDK project does not compile:\n{built.stdout[-1500:]}")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.legacy = cls._synth("legacy")
        cls.acme = cls._synth("acme", AGENTCORE_CLIENT="acme", AGENTCORE_CLIENT_NAME="Acme Super")
        cls.globex = cls._synth("globex", AGENTCORE_CLIENT="globex")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        if cls._made_targets:
            TARGETS.unlink(missing_ok=True)

    @classmethod
    def _run(cls, outdir: Path, **env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("AGENTCORE_CLIENT")}
        return subprocess.run(
            ["node", "dist/bin/cdk.js"], cwd=CDK, capture_output=True, text=True, check=False,
            env={**clean, "AWS_REGION": REGION, "CDK_OUTDIR": str(outdir), **env},
        )

    @classmethod
    def _synth(cls, label: str, **env) -> dict:
        outdir = Path(cls._tmp.name) / label
        result = cls._run(outdir, **env)
        if result.returncode != 0:
            raise RuntimeError(f"synth of {label} failed:\n{result.stderr[-1500:]}")
        template = json.loads((outdir / f"{STACK}.template.json").read_text(encoding="utf-8"))
        manifest = json.loads((outdir / "manifest.json").read_text(encoding="utf-8"))
        template["_stack_tags"] = next(
            a["properties"].get("tags", {}) for a in manifest["artifacts"].values() if a.get("type") == "aws:cloudformation:stack"
        )
        return template

    # ---- names ---------------------------------------------------------------------------------------------------
    def test_with_no_client_the_names_are_exactly_the_original(self):
        self.assertEqual(_names(self.legacy), ORIGINAL_NAMES)

    def test_with_a_client_every_name_carries_it(self):
        for template, slug in ((self.acme, "acme"), (self.globex, "globex")):
            with self.subTest(client=slug):
                names = _names(template)
                self.assertTrue(names)
                self.assertTrue(all(n.startswith(f"{slug}-") for n in names), sorted(n for n in names if not n.startswith(f"{slug}-")))

    def test_two_clients_and_the_original_share_no_name(self):
        sets = {"legacy": _names(self.legacy), "acme": _names(self.acme), "globex": _names(self.globex)}
        for a, b in (("acme", "globex"), ("acme", "legacy"), ("globex", "legacy")):
            with self.subTest(pair=(a, b)):
                self.assertEqual(sets[a] & sets[b], set())

    # ---- the client's own key --------------------------------------------------------------------------------------
    def _client_keys(self, template):
        return [r for r in _of_type(template, "AWS::KMS::Key") if r["Properties"].get("Description", "").startswith("Data key for client")]

    def test_a_client_gets_a_rotating_key_and_the_original_does_not(self):
        keys = self._client_keys(self.acme)
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0]["Properties"]["EnableKeyRotation"])
        self.assertEqual(self._client_keys(self.legacy), [])

    def test_in_client_mode_every_data_store_uses_that_key(self):
        a = self.acme
        for table in _of_type(a, "AWS::DynamoDB::Table"):
            self.assertIn("KMSMasterKeyId", table["Properties"]["SSESpecification"])
        self.assertTrue(_of_type(a, "AWS::S3::Bucket")[0]["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"])
        self.assertIn("KmsMasterKeyId", _of_type(a, "AWS::SNS::Topic")[0]["Properties"])
        self.assertIn("KmsMasterKeyId", _of_type(a, "AWS::SQS::Queue")[0]["Properties"])

    def test_in_legacy_mode_the_data_stores_are_not_changed(self):
        for table in _of_type(self.legacy, "AWS::DynamoDB::Table"):
            self.assertNotIn("SSESpecification", table["Properties"])
        self.assertNotIn("KmsMasterKeyId", _of_type(self.legacy, "AWS::SNS::Topic")[0]["Properties"])

    # ---- wording, memory alias, tags ---------------------------------------------------------------------------------
    @staticmethod
    def _guardrail(template):
        return _of_type(template, "AWS::Bedrock::Guardrail")[0]["Properties"]

    def test_the_guardrail_keeps_the_original_wording_without_a_client(self):
        self.assertIn("needs a HESTA team member", self._guardrail(self.legacy)["BlockedInputMessaging"])

    def test_the_guardrail_uses_the_clients_name_or_generic_wording(self):
        self.assertIn("a member of the Acme Super team", self._guardrail(self.acme)["BlockedInputMessaging"])
        self.assertIn("a member of our team", self._guardrail(self.globex)["BlockedInputMessaging"])
        self.assertNotIn("HESTA", json.dumps(self._guardrail(self.globex)))

    def test_every_mode_publishes_the_generic_memory_id(self):
        for label, template in (("legacy", self.legacy), ("acme", self.acme), ("globex", self.globex)):
            with self.subTest(mode=label):
                runtime = _of_type(template, "AWS::BedrockAgentCore::Runtime")[0]
                self.assertIn("AGENTCORE_MEMORY_ID", runtime["Properties"]["EnvironmentVariables"])

    def test_the_client_is_a_stack_tag_and_an_output_only_in_client_mode(self):
        self.assertEqual(self.acme["_stack_tags"]["agentcore:client"], "acme")
        self.assertNotIn("agentcore:client", self.legacy["_stack_tags"])
        self.assertTrue(any("ClientSlug" in k for k in self.acme["Outputs"]))
        self.assertFalse(any("ClientSlug" in k for k in self.legacy["Outputs"]))

    # ---- refusing a bad slug -----------------------------------------------------------------------------------------
    def test_a_malformed_client_stops_the_build_with_a_readable_message(self):
        for bad in ("Acme_Corp", "claimsagent", "x"):
            with self.subTest(client=bad), tempfile.TemporaryDirectory() as tmp:
                result = self._run(Path(tmp) / "out", AGENTCORE_CLIENT=bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid client", result.stderr)


if __name__ == "__main__":
    unittest.main()
