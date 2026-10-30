"""The illustrative insurer's demo assets: its Cedar pack, its synthetic data and its sample emails.

What is proved:
  * Every sample email goes through the REAL trigger parsing, the normaliser, the identity check and the routing gate, and comes out as
    `clients/fininsurer/demo/scenarios.json` says. That file also records what the message text alone would do if the classifier set no
    flag, and the README's "needs the classifier" column is checked against it, so the demo does not oversell the code.
  * The data is synthetic only: the seed script refuses a record that is not marked synthetic, has a real-looking email, repeats a number
    or points at a policy that is not there, and every email in the folder uses a reserved example domain.
  * The seed script never touches the shared tables, never reaches AWS for --list or --dry-run, and --reset removes only synthetic records.
  * The Cedar pack compiles to exactly what is committed, its examples pass against the real handler, it is wired to the one agent the
    overlay gives it, its vocabulary matches the real Gateway tools, and it carries NO approval (an approval must come from a person, so
    a committed one would be a forgery).

All names, numbers and addresses are invented.

Run:
    python3 -m unittest tests.test_fininsurer_demo -v

Needs PyYAML and `strands-agents[cedar]`; the parts that need more skip with a reason.
"""

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "04_hesta_member_email"
DEMO = ROOT / "clients" / "fininsurer" / "demo"
PACK = ROOT / "clients" / "fininsurer" / "pack"
OVERLAY = ROOT / "clients" / "fininsurer" / "pipeline" / "pipeline.yaml"
EXAMPLE_DOMAINS = ("example.com", "example.net", "example.org")
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_policyholders = _load("seed_policyholders", ROOT / "scripts" / "seed_policyholders.py")
assemble_client = _load("assemble_client_demo", ROOT / "scripts" / "assemble_client.py")
trigger = _load("trigger_handler_demo", ROOT / "lambdas" / "trigger" / "handler.py")  # the real parse_email; its S3 client is lazy

SKIP_REASON = ""
try:
    import yaml  # noqa: E402
    from core import memory  # noqa: E402
    from fininsurer import tools  # noqa: E402
except ImportError as exc:  # pragma: no cover - depends on the interpreter
    SKIP_REASON = f"needs PyYAML and strands-agents ({exc})"

DATA = json.loads((DEMO / "policyholders.json").read_text(encoding="utf-8"))
SCENARIOS = json.loads((DEMO / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
HOLDERS = {h["policy_number"]: h for h in DATA["policyholders"]}
README = (DEMO / "README.md").read_text(encoding="utf-8")


# ─── Synthetic only ──────────────────────────────────────────────────────────


class SyntheticOnlyTests(unittest.TestCase):
    def test_the_committed_data_passes_the_seed_scripts_own_checks(self):
        data = seed_policyholders.load_data(DEMO / "policyholders.json")
        self.assertEqual(len(data["policyholders"]), 14)
        self.assertEqual(len(data["claims"]), 2)

    def test_every_email_address_in_the_demo_folder_is_on_a_reserved_domain(self):
        found = 0
        for path in [DEMO / "policyholders.json", *sorted((DEMO / "emails").glob("*.txt"))]:
            for domain in re.findall(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", path.read_text(encoding="utf-8")):
                found += 1
                with self.subTest(file=path.name, domain=domain):
                    # illustrative-insurer.example is the demo company's own (the To: header); .example is reserved too.
                    self.assertTrue(domain.lower() in EXAMPLE_DOMAINS or domain.lower() == "illustrative-insurer.example", domain)
        self.assertGreater(found, 25)

    def test_policy_numbers_are_unique_and_in_the_one_invented_format(self):
        numbers = [h["policy_number"] for h in DATA["policyholders"]]
        self.assertEqual(len(numbers), len(set(numbers)))
        for number in numbers:
            self.assertRegex(number, r"^POL-1\d{4}$")

    def _load_mutated(self, mutate) -> str:
        """Apply a change to a copy of the data and return the message the seed script refuses it with."""
        data = json.loads(json.dumps(DATA))
        mutate(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(seed_policyholders.SeedError) as ctx:
                seed_policyholders.load_data(path)
        return str(ctx.exception)

    def test_a_record_not_marked_synthetic_is_refused(self):
        self.assertIn('is not marked "synthetic": true', self._load_mutated(lambda d: d["policyholders"][0].pop("synthetic")))

    def test_a_real_looking_email_is_refused(self):
        self.assertIn("a demo may use only example.com", self._load_mutated(lambda d: d["policyholders"][1].update(email="someone@gmail.com")))

    def test_a_repeated_policy_number_and_a_claim_for_a_missing_policy_are_refused(self):
        self.assertIn("repeats policy_number POL-10001", self._load_mutated(lambda d: d["policyholders"][1].update(policy_number="POL-10001")))
        self.assertIn("which is not in policyholders", self._load_mutated(lambda d: d["claims"][0].update(policy_number="POL-99999")))

    def test_every_problem_is_reported_together(self):
        def mutate(d):
            d["policyholders"][0].pop("synthetic")
            d["policyholders"][1]["email"] = "x@gmail.com"
            d["claims"][0]["policy_number"] = "POL-99999"

        self.assertGreaterEqual(self._load_mutated(mutate).count("\n - "), 3)


# ─── The seed script ─────────────────────────────────────────────────────────


class _Ctx:
    """The part of a client context the seed script uses."""

    client = "fininsurer"
    stack_name = "AgentCore-Fininsurer-dev"

    @staticmethod
    def table(kind):
        return f"fininsurer-dev-{kind}"


class SeedScriptTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", err), mock.patch.dict(os.environ):
            os.environ.pop("AGENTCORE_CLIENT", None)
            code = seed_policyholders.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_it_refuses_to_run_without_a_client_so_it_never_touches_the_shared_tables(self):
        code, _, err = self.run_main("--list")
        self.assertEqual(code, 2)
        self.assertIn("--client is required", err)

    def test_list_prints_every_policyholder_and_needs_no_aws(self):
        with mock.patch.dict(sys.modules, {"boto3": None}):  # importing boto3 would now fail
            code, out, _ = self.run_main("--client", "fininsurer", "--list")
        self.assertEqual(code, 0)
        for number in HOLDERS:
            self.assertIn(number, out)
        self.assertIn("CLM-DEMO0001", out)

    def test_dry_run_names_this_clients_exact_tables_and_makes_no_aws_call(self):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict(sys.modules, {"boto3": None}):
            seed_policyholders.seed("ap-southeast-2", _Ctx, DATA, dry_run=True, do_reset=True)
        text = out.getvalue()
        self.assertIn("fininsurer-dev-Policies", text)
        self.assertIn("fininsurer-dev-Claims", text)
        self.assertIn("no AWS calls, no writes", text)
        self.assertIn("reset", text)

    def test_a_real_run_writes_every_record_to_this_clients_own_tables_only(self):
        tables, batches = {}, {}

        def table_factory(name):
            batch = mock.MagicMock()
            table = mock.MagicMock()
            table.batch_writer.return_value.__enter__.return_value = batch
            tables[name], batches[name] = table, batch
            return table

        fake_boto3 = mock.MagicMock()
        fake_boto3.resource.return_value.Table.side_effect = table_factory
        with mock.patch.dict(sys.modules, {"boto3": fake_boto3}), redirect_stdout(io.StringIO()):
            seed_policyholders.seed("ap-southeast-2", _Ctx, DATA, dry_run=False, do_reset=False)
        self.assertEqual(set(tables), {"fininsurer-dev-Policies", "fininsurer-dev-Claims"})
        written = [call.kwargs["Item"]["policy_number"] for call in batches["fininsurer-dev-Policies"].put_item.call_args_list]
        self.assertEqual(sorted(written), sorted(HOLDERS))
        self.assertEqual(batches["fininsurer-dev-Claims"].put_item.call_count, 2)

    def test_reset_deletes_only_records_marked_synthetic_and_follows_every_page(self):
        table = mock.MagicMock()
        table.scan.side_effect = [
            {"Items": [{"policy_number": "POL-1"}, {"policy_number": "POL-2"}], "LastEvaluatedKey": {"policy_number": "POL-2"}},
            {"Items": [{"policy_number": "POL-3"}]},
        ]
        batch = mock.MagicMock()
        table.batch_writer.return_value.__enter__.return_value = batch
        removed = seed_policyholders.reset(table, "policy_number")
        self.assertEqual(removed, 3)
        expression = table.scan.call_args_list[0].kwargs["FilterExpression"].get_expression()
        self.assertEqual(expression["operator"], "=")
        self.assertIs(expression["values"][1], True)  # only records marked synthetic
        self.assertEqual(table.scan.call_args_list[1].kwargs["ExclusiveStartKey"], {"policy_number": "POL-2"})
        self.assertEqual([c.kwargs["Key"] for c in batch.delete_item.call_args_list], [{"policy_number": f"POL-{n}"} for n in (1, 2, 3)])


# ─── Every sample email, end to end through the deterministic steps ─────────


def _read_email(name: str):
    """What the trigger Lambda hands the agent for this file, using the trigger's own parsing."""
    content = (DEMO / "emails" / name).read_text(encoding="utf-8")
    if trigger.is_email_format(content):
        headers, body = trigger.parse_email(content)
        return f"Process this insurance claim from email:\n\n{body}", headers.get("from", "")
    return content, ""


def _verify(fields: dict, sender_authenticated: bool = False) -> dict:
    number = fields["policy_number_for_lookup"] or ""
    record = HOLDERS.get(number)
    return tools.classify_verification(
        policy_number=number, sender_email=fields["from_email"] or "", record_found=record is not None,
        record_email=(record or {}).get("email", ""), record_status=(record or {}).get("status", ""),
        record_policy_type=(record or {}).get("policy_type", ""), sender_authenticated=sender_authenticated,
    )


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class SampleEmailTests(unittest.TestCase):
    def setUp(self):
        memory.forget_all()
        self.addCleanup(memory.forget_all)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)

    def profile(self, name):
        os.environ["DEPLOYMENT_PROFILE"] = name

    def test_the_scenario_list_and_the_email_folder_match_exactly(self):
        on_disk = {p.name for p in (DEMO / "emails").glob("*.txt")}
        listed = {s["file"] for s in SCENARIOS}
        self.assertEqual(on_disk, listed)
        self.assertEqual(len(SCENARIOS), 15)

    def test_every_scenario_points_at_a_policy_that_exists(self):
        for s in SCENARIOS:
            number = s["expect"]["policy_number"]
            with self.subTest(file=s["file"]):
                self.assertTrue(number is None or number in HOLDERS, number)

    def test_each_email_is_read_and_checked_as_the_scenario_says(self):
        for s in SCENARIOS:
            expect = s["expect"]
            with self.subTest(file=s["file"]):
                raw, sender = _read_email(s["file"])
                fields = tools.normalize_email(raw=raw, sender_email=sender)
                self.assertEqual(
                    (fields["policy_number_for_lookup"], fields["channel"], fields["sender_type"], fields["attachment_count"]),
                    (expect["policy_number"], expect["channel"], expect["sender_type"], expect["attachments"]),
                )

                for profile in ("presale", "strict"):
                    self.profile(profile)
                    self.assertEqual(_verify(fields)["verification_level"], expect[f"verification_{profile}"], profile)

                self.profile("presale")  # the demo profile
                verification = _verify(fields)
                flags = s["flags"]
                route = tools.route_decision(
                    primary_intent_id=s["intent"], primary_confidence=90,
                    hardship_indicated=flags.get("hardship_indicated", False),
                    personal_advice_requested=flags.get("personal_advice_requested", False),
                    instructs_cancellation=flags.get("instructs_cancellation", False),
                    contact_detail_change=flags.get("contact_detail_change", False),
                    verification_required=verification["verification_required"],
                    verification_level=verification["verification_level"],
                    vulnerability_flags=s["vulnerability_flags"], priority=s["priority"],
                    complaint_indicator=s["complaint_indicator"], sender_type=fields["sender_type"],
                    latest_message=fields["latest_message"],
                )
                self.assertEqual((route["escalate_to_human"], route["hardship"], route["priority_floor"]),
                                 (expect["escalate"], expect["hardship"], expect["priority_floor"]), route["reasons"])

    def test_what_the_message_text_alone_would_do_if_the_classifier_set_no_flag(self):
        # The routing gate's backstops. A routine intent, nothing else flagged, identity treated as fine: only the words can escalate.
        for s in SCENARIOS:
            with self.subTest(file=s["file"]):
                raw, sender = _read_email(s["file"])
                fields = tools.normalize_email(raw=raw, sender_email=sender)
                route = tools.route_decision(
                    primary_intent_id="claim_status", primary_confidence=90, verification_required=False, verification_level="verified",
                    latest_message=fields["latest_message"],
                )
                self.assertEqual((route["escalate_to_human"], route["hardship"]),
                                 (s["backstop_alone"]["escalate"], s["backstop_alone"]["hardship"]), route["reasons"])

    @staticmethod
    def _relies_on_classifier():
        """Escalated, but not by the message text and not for identity or sender reasons: only the intent or a flag did it."""
        return {
            s["file"] for s in SCENARIOS
            if s["expect"]["escalate"] and not s["backstop_alone"]["escalate"]
            and s["expect"]["verification_presale"] == "verified" and s["expect"]["sender_type"] != "third_party"
        }

    def test_the_demo_has_both_light_path_cases_and_cases_that_rely_on_the_classifier(self):
        light = [s for s in SCENARIOS if not s["expect"]["escalate"]]
        self.assertGreaterEqual(len(light), 2)
        self.assertGreaterEqual(len(self._relies_on_classifier()), 3)  # an honest demo shows the cases the code alone would miss

    def test_the_readmes_needs_the_classifier_column_matches_what_the_code_does(self):
        rows = [line for line in README.splitlines() if line.startswith("| `")]
        self.assertEqual(len(rows), 15)
        claimed = set()
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            stem = cells[0].strip("`")
            self.assertTrue(any(s["file"] == f"{stem}.txt" for s in SCENARIOS), stem)
            if cells[-1] == "**Yes**":
                claimed.add(f"{stem}.txt")
        self.assertEqual(claimed, self._relies_on_classifier())


# ─── The Cedar pack ──────────────────────────────────────────────────────────


@unittest.skipIf(SKIP_REASON, SKIP_REASON)
class CedarPackTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
        self.policy = (PACK / "compiled" / "policy.cedar").read_text(encoding="utf-8")

    def test_the_pack_carries_no_approval_because_a_person_must_give_one(self):
        # A committed approval here would be unsigned JSON claiming a reviewer signed off. Nobody did.
        self.assertEqual(self.manifest["approvals"], [])

    def test_the_loader_therefore_refuses_to_load_it(self):
        try:
            from core import cedar_pack
        except ImportError as exc:
            self.skipTest(f"needs the cedar extra ({exc})")
        with self.assertRaises(cedar_pack.PackRejected) as ctx:
            cedar_pack.verify_pack(PACK)
        self.assertIn("approval", str(ctx.exception))

    def test_recompiling_gives_exactly_what_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "pack"
            shutil.copytree(PACK, copy)
            result = subprocess.run([sys.executable, str(ROOT / "rules" / "cedar_pack.py"), "compile", str(copy)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-600:])
            self.assertEqual((copy / "compiled" / "policy.cedar").read_bytes(), (PACK / "compiled" / "policy.cedar").read_bytes())
            self.assertEqual((copy / "manifest.json").read_bytes(), (PACK / "manifest.json").read_bytes())

    def test_every_example_rule_passes_against_the_real_cedar_handler(self):
        try:
            import cedarpy  # noqa: F401, PLC0415
        except ImportError as exc:
            self.skipTest(f"needs the cedar extra ({exc})")
        result = subprocess.run([sys.executable, str(ROOT / "rules" / "cedar_pack.py"), "check", str(PACK)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout[-1500:])
        self.assertIn(" 0 failed", result.stdout)

    def test_the_compiled_policy_says_what_the_feature_file_says(self):
        self.assertEqual(self.policy.count('permit (principal == Agent::"human_handoff"'), 2)  # create_claim and request_human_review
        self.assertIn('context.input.status == "pending_review"', self.policy)
        self.assertIn('context.input.decision == "escalated"', self.policy)
        self.assertIn('forbid (principal, action == Action::"resolve-claim___resolve_claim"', self.policy)
        self.assertIn('forbid (principal, action == Action::"notification___send_notification"', self.policy)
        for read in ("policy-lookup", "list-pending-claims"):
            self.assertNotIn(read, self.policy)  # reads are not permitted to anyone by this pack

    def test_the_pack_is_wired_to_exactly_the_agent_the_overlay_gives_it(self):
        vocabulary = yaml.safe_load((PACK / "vocabulary.yaml").read_text(encoding="utf-8"))
        overlay = yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))
        carriers = sorted(n for n, a in overlay["agents"].items() if "cedar_pack" in (a.get("agent_kwargs") or {}))
        self.assertEqual(carriers, sorted(vocabulary["agents"]))
        self.assertEqual(carriers, ["human_handoff"])

    def test_the_pack_names_no_other_client_and_says_it_is_illustrative(self):
        for path in (PACK / "vocabulary.yaml", PACK / "fininsurer_tool_boundary.feature"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("ILLUSTRATIVE", text.upper(), path.name)
            self.assertNotIn("hesta", text.lower(), path.name)

    def test_the_vocabulary_matches_the_real_gateway_tools_arguments_and_action_names(self):
        vocabulary = yaml.safe_load((PACK / "vocabulary.yaml").read_text(encoding="utf-8"))
        spec = json.loads((ROOT / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
        expected_actions, real_args = {}, {}
        for target in spec["agentCoreGateways"][0]["targets"]:
            schema_file = ROOT / target["lambdaFunctionArn"]["toolSchemaFile"]
            for tool_def in json.loads(schema_file.read_text(encoding="utf-8")):
                expected_actions[tool_def["name"]] = f"{target['name']}___{tool_def['name']}"
                real_args[tool_def["name"]] = set(tool_def["inputSchema"]["properties"])
        self.assertEqual(set(vocabulary["tools"]), set(expected_actions))
        for name, body in vocabulary["tools"].items():
            with self.subTest(tool=name):
                self.assertEqual(body["action"], expected_actions[name])  # the Cedar action is the Gateway's own tool name
                self.assertEqual(set(body["args"]), real_args[name])  # no rule can mention an argument the tool does not take

    def test_assembling_ships_the_pack_switched_off_and_refuses_to_switch_it_on(self):
        try:
            from core import cedar_pack  # noqa: F401, PLC0415
        except ImportError as exc:
            self.skipTest(f"needs the cedar extra ({exc})")
        with tempfile.TemporaryDirectory() as tmp:
            info = assemble_client.assemble("fininsurer", Path(tmp) / "out", display_name="Illustrative Insurer", profile="presale")
            self.assertTrue(info["pack_included"])
            self.assertFalse(info["pack_enabled"])
            self.assertTrue((Path(info["out"]) / "app" / "04_hesta_member_email" / "client_pack" / "manifest.json").is_file())
            with self.assertRaisesRegex(assemble_client.AssembleError, "agent would not start"):
                assemble_client.assemble("fininsurer", Path(tmp) / "out2", display_name="Illustrative Insurer", profile="presale",
                                         enable_pack=True)


if __name__ == "__main__":
    unittest.main()
