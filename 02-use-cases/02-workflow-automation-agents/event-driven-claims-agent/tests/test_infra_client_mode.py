"""The CDK stack in legacy mode and in client mode (ADR-0015, Q7). Offline: it synthesises templates, deploys nothing.

What is proved, from the synthesised CloudFormation:
  * With no client, the physical names are exactly the original ones (so an existing deployment is not replaced).
  * With a client, every name the stack controls carries the client, and two clients in one account and region share none.
  * In client mode the data stores use a key of the client's own; in legacy mode there is no such key.
  * The guardrail speaks in the client's name, and keeps the original wording when there is no client.
  * A client's own guardrail file replaces the wording (topics, examples, messages); the blocked-output sentinel is never configurable;
    a missing or invalid file stops the build with a readable message instead of falling back.
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
# A made-up insurer's wording, to prove a client file replaces the built-in text. Nothing here is a real client's.
INSURER_WORDING = {
    "version": 1,
    "description": "Denies personal advice on cover; {staff} staff handle these enquiries.",
    "blockedInputMessage": "This enquiry needs {teamMember}. We cannot advise on what cover you should hold.",
    "topics": [
        {
            "name": "CoverAdvice",
            "definition": "Advice on which insurance cover to buy, keep, reduce or cancel, tailored to the person asking.",
            "examples": ["Should I cancel my policy?", "Which level of cover is best for me?", "Is my excess too high for my situation?"],
        }
    ],
}
SENTINEL = "[GUARDRAIL_BLOCKED_ADVICE]"
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
        cls.insurer_file = Path(cls._tmp.name) / "insurer-topics.json"
        cls.insurer_file.write_text(json.dumps(INSURER_WORDING), encoding="utf-8")
        cls.insurer = cls._synth(
            "insurer", AGENTCORE_CLIENT="insurer", AGENTCORE_CLIENT_NAME="Acme Insurance", AGENTCORE_GUARDRAIL_TOPICS=str(cls.insurer_file)
        )
        cls.from_template = cls._synth(
            "template", AGENTCORE_CLIENT="tmpl", AGENTCORE_GUARDRAIL_TOPICS=str(ROOT / "clients" / "_template" / "guardrail" / "topics.json")
        )

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

    # ---- a client's own guardrail wording ------------------------------------------------------------------------------
    @staticmethod
    def _topics(template):
        return _of_type(template, "AWS::Bedrock::Guardrail")[0]["Properties"]["TopicPolicyConfig"]["TopicsConfig"]

    def test_a_client_file_replaces_the_topics_examples_and_messages(self):
        g = self._guardrail(self.insurer)
        topic = self._topics(self.insurer)
        self.assertEqual([t["Name"] for t in topic], ["CoverAdvice"])
        self.assertEqual(topic[0]["Examples"], INSURER_WORDING["topics"][0]["examples"])
        self.assertEqual(topic[0]["Definition"], INSURER_WORDING["topics"][0]["definition"])
        self.assertIn("a member of the Acme Insurance team", g["BlockedInputMessaging"])
        self.assertIn("Acme Insurance staff", g["Description"])
        # Nothing of the built-in wording, in either variant, survives next to the client's own.
        self.assertNotRegex(json.dumps(g), r"(?i)super|HESTA|high growth|roll over")

    def test_the_blocked_output_sentinel_is_the_same_in_every_mode(self):
        # The Reviewer agent matches this string by value, so a client file must not be able to change it.
        for label, template in (("legacy", self.legacy), ("acme", self.acme), ("globex", self.globex), ("insurer", self.insurer)):
            with self.subTest(mode=label):
                self.assertEqual(self._guardrail(template)["BlockedOutputsMessaging"], SENTINEL)

    def test_the_template_file_gives_exactly_the_neutral_default(self):
        # Copying clients/_template/guardrail/ unchanged must be harmless: same wording as having no file at all.
        self.assertEqual(self._topics(self.from_template), self._topics(self.globex))
        self.assertEqual(self._guardrail(self.from_template)["Description"], self._guardrail(self.globex)["Description"])

    def test_a_client_file_changes_only_the_guardrail(self):
        # Same client name, with and without the file: nothing but the guardrail's wording may differ.
        def without_guardrail(template):
            return {k: v for k, v in template["Resources"].items() if v["Type"] != "AWS::Bedrock::Guardrail"}
        plain = self._synth("insurer-plain", AGENTCORE_CLIENT="insurer", AGENTCORE_CLIENT_NAME="Acme Insurance")
        self.assertEqual(without_guardrail(plain), without_guardrail(self.insurer))

    def test_a_missing_or_invalid_file_stops_the_build_and_never_falls_back(self):
        bad = Path(self._tmp.name) / "bad.json"
        bad.write_text(json.dumps({**INSURER_WORDING, "topics": [{**INSURER_WORDING["topics"][0], "examples": list("abcdef")}]}), encoding="utf-8")
        cases = {"missing": (str(Path(self._tmp.name) / "typo.json"), "cannot be read"), "invalid": (str(bad), "topics[0].examples has 6 entries")}
        for label, (path, expected) in cases.items():
            with self.subTest(case=label):
                result = self._run(Path(self._tmp.name) / f"fail-{label}", AGENTCORE_CLIENT="acme", AGENTCORE_GUARDRAIL_TOPICS=path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

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
