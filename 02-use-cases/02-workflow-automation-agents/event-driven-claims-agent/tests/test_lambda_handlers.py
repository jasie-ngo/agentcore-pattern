"""Unit tests for the Gateway tool Lambdas built from blueprints.

Each blueprint is loaded with the REAL settings the deployed tool gets — the
``tool_config`` from agentcore/generated/manifest.json — so these tests cover
both the blueprint code and each use case's gateway_tools.yaml entry.
DynamoDB/SNS are mocked; no AWS access is needed.

Run:
    python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import MagicMock

# boto3.resource() can require a region at creation time; set one before import.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")

_ROOT = os.path.join(os.path.dirname(__file__), "..")

try:
    import boto3
    from botocore.exceptions import ClientError

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False


def _manifest_tool(name: str) -> dict:
    with open(os.path.join(_ROOT, "agentcore", "generated", "manifest.json"), encoding="utf-8") as fh:
        return next(t for t in json.load(fh)["tools"] if t["name"] == name)


def _load_blueprint(blueprint: str, tool_config: dict, module_name: str):
    """Load a blueprint handler with the given settings, under a unique module name."""
    os.environ["TOOL_CONFIG"] = json.dumps(tool_config)
    os.environ["TABLE_NAME"] = "test-table"
    path = os.path.join(_ROOT, "lambdas", "blueprints", blueprint, "handler.py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.table = MagicMock()
    return mod


def _call(mod, event):
    return json.loads(mod.handler(event, None))


def _conditional_failure():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class CreateClaimTests(unittest.TestCase):
    """claims_agent create_claim — record_write, find-or-create on policy_number + source."""

    def setUp(self):
        self.mod = _load_blueprint("record_write", _manifest_tool("create_claim")["tool_config"], "create_claim_bp")

    def _valid(self, **extra):
        return {
            "policy_number": "POL-1",
            "description": "d",
            "estimated_amount": 100,
            "category": "theft",
            **extra,
        }

    def test_missing_required_fields(self):
        result = _call(self.mod, {"description": "damage"})
        self.assertIn("error", result)
        self.assertIn("policy_number", result["error"])
        self.mod.table.put_item.assert_not_called()

    def test_description_too_long(self):
        result = _call(self.mod, self._valid(description="x" * 5001))
        self.assertIn("5000", result["error"])
        self.mod.table.put_item.assert_not_called()

    def test_negative_amount(self):
        self.assertIn("error", _call(self.mod, self._valid(estimated_amount=-5)))
        self.mod.table.put_item.assert_not_called()

    def test_amount_exceeds_max(self):
        self.assertIn("error", _call(self.mod, self._valid(estimated_amount=10_000_001)))
        self.mod.table.put_item.assert_not_called()

    def test_non_numeric_amount(self):
        self.assertIn("error", _call(self.mod, self._valid(estimated_amount="lots")))

    def test_valid_claim_creates_record(self):
        result = _call(self.mod, self._valid(category="auto_collision"))
        self.assertNotIn("error", result)
        self.assertEqual(result["result"], "created")
        self.assertTrue(result["claim_id"].startswith("CLM-"))
        self.assertEqual(result["category"], "auto_collision")
        self.assertIn("created_at", result)
        self.mod.table.put_item.assert_called_once()

    def test_agent_routed_status_passthrough(self):
        result = _call(self.mod, self._valid(status="approved", decision="auto_approved"))
        self.assertEqual((result["status"], result["decision"]), ("approved", "auto_approved"))

    def test_defaults_applied(self):
        result = _call(self.mod, self._valid())
        self.assertEqual((result["status"], result["decision"]), ("pending_review", "agent_routed"))

    def test_same_policy_and_source_gives_same_claim_id(self):
        a = _call(self.mod, self._valid(source="s3://b/claims-inbox/x.eml"))
        b = _call(self.mod, self._valid(source="s3://b/claims-inbox/x.eml", description="other"))
        c = _call(self.mod, self._valid(source="s3://b/claims-inbox/y.eml"))
        self.assertEqual(a["claim_id"], b["claim_id"])
        self.assertNotEqual(a["claim_id"], c["claim_id"])

    def test_without_source_ids_are_random(self):
        self.assertNotEqual(_call(self.mod, self._valid())["claim_id"], _call(self.mod, self._valid())["claim_id"])

    def test_redelivery_returns_existing_claim(self):
        self.mod.table.put_item.side_effect = _conditional_failure()
        self.mod.table.get_item.return_value = {"Item": {"claim_id": "CLM-OLD", "status": "approved"}}
        result = _call(self.mod, self._valid(source="s3://b/k"))
        self.assertEqual(result["result"], "existing")
        self.assertEqual(result["claim_id"], "CLM-OLD")

    def test_unknown_fields_are_ignored(self):
        result = _call(self.mod, self._valid(internal_flag="x"))
        self.assertNotIn("internal_flag", result)


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class HumanReviewTests(unittest.TestCase):
    """claims_agent request_human_review — record_write with derived priority + SNS notify."""

    def setUp(self):
        cfg = _manifest_tool("request_human_review")["tool_config"]
        self.mod = _load_blueprint("record_write", cfg, "human_review_bp")
        self.mod.sns = MagicMock()
        self.mod.TOPIC_ARN = "arn:aws:sns:us-west-2:123:topic"

    def test_high_amount_is_high_priority_and_notifies(self):
        result = _call(self.mod, {"claim_id": "CLM-1", "reason": "r", "estimated_amount": 60000})
        self.assertEqual((result["priority"], result["status"]), ("high", "pending"))
        self.assertTrue(result["review_id"].startswith("REV-"))
        subject = self.mod.sns.publish.call_args.kwargs["Subject"]
        self.assertEqual(subject, "[high] Review needed: CLM-1")

    def test_low_amount_is_medium_priority(self):
        self.assertEqual(
            _call(self.mod, {"claim_id": "C", "reason": "r", "estimated_amount": 10})["priority"], "medium"
        )

    def test_caller_cannot_override_fixed_status(self):
        result = _call(self.mod, {"claim_id": "C", "reason": "r", "estimated_amount": 1, "status": "resolved"})
        self.assertEqual(result["status"], "pending")


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class RecordWriteModeTests(unittest.TestCase):
    """if_exists: error — reject duplicates instead of returning them."""

    def test_error_mode_rejects_duplicates(self):
        cfg = {
            "inputs": {"member_id": {"type": "string", "required": True}},
            "id": {"field": "case_id", "prefix": "CASE", "from": ["member_id"]},
            "if_exists": "error",
        }
        mod = _load_blueprint("record_write", cfg, "error_mode_bp")
        mod.table.put_item.side_effect = _conditional_failure()
        self.assertIn("already exists", _call(mod, {"member_id": "6001"})["error"])


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class LookupPolicyTests(unittest.TestCase):
    """claims_agent lookup_policy — retrieval by key, returning only listed fields."""

    def setUp(self):
        self.mod = _load_blueprint("retrieval", _manifest_tool("lookup_policy")["tool_config"], "lookup_policy_bp")

    def test_missing_policy_number(self):
        result = _call(self.mod, {})
        self.assertFalse(result["found"])
        self.assertIn("policy_number", result["error"])
        self.mod.table.get_item.assert_not_called()

    def test_policy_not_found(self):
        self.mod.table.get_item.return_value = {}
        result = _call(self.mod, {"policy_number": "POL-NOPE"})
        self.assertFalse(result["found"])
        self.assertIn("not found", result["error"].lower())

    def test_found_returns_only_listed_fields(self):
        self.mod.table.get_item.return_value = {
            "Item": {
                "policy_number": "POL-12345",
                "status": "active",
                "coverage_amount": Decimal(50000),
                "date_of_birth": "1975-03-12",
            }
        }
        result = _call(self.mod, {"policy_number": "POL-12345"})
        self.assertTrue(result["found"])
        self.assertEqual((result["policy_number"], result["status"]), ("POL-12345", "active"))
        self.assertEqual(result["coverage_amount"], "50000")
        self.assertNotIn("date_of_birth", result)


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class ListPendingClaimsTests(unittest.TestCase):
    """claims_agent list_pending_claims — retrieval in list mode over the status index."""

    def setUp(self):
        cfg = _manifest_tool("list_pending_claims")["tool_config"]
        self.mod = _load_blueprint("retrieval", cfg, "list_pending_bp")

    def test_lists_pending_claims_under_claims_key(self):
        self.mod.table.query.return_value = {"Items": [{"claim_id": "CLM-1", "policy_number": "P", "status": "x"}]}
        result = _call(self.mod, {})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["claims"][0]["claim_id"], "CLM-1")
        self.assertNotIn("status", result["claims"][0])
        self.assertEqual(self.mod.table.query.call_args.kwargs["IndexName"], "status-index")

    def test_empty(self):
        self.mod.table.query.return_value = {"Items": []}
        self.assertEqual(_call(self.mod, {}), {"found": False, "count": 0, "claims": []})


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class RetrievalIndexLookupTests(unittest.TestCase):
    """Two lookup keys: GetItem by id, or Query on an index by email."""

    def setUp(self):
        cfg = {"lookup": [{"key": "member_id"}, {"key": "email", "index": "email-index"}], "returns": ["member_id"]}
        self.mod = _load_blueprint("retrieval", cfg, "member_lookup_bp")

    def test_looks_up_by_index_when_only_email_given(self):
        self.mod.table.query.return_value = {"Items": [{"member_id": "6001", "email": "a@b.co"}]}
        result = _call(self.mod, {"email": "a@b.co"})
        self.assertEqual(result, {"found": True, "member_id": "6001"})
        self.mod.table.get_item.assert_not_called()

    def test_prefers_first_key(self):
        self.mod.table.get_item.return_value = {"Item": {"member_id": "6001"}}
        _call(self.mod, {"member_id": "6001", "email": "a@b.co"})
        self.mod.table.query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
