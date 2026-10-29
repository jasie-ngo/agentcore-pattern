"""Tests for scripts/build_tools.py — the YAML forms → Gateway tools generator.

Checks that the committed generated files match the YAML forms (so a forgotten
rebuild fails CI, not a deploy), and that invalid forms are rejected with a
clear message. Needs PyYAML (present in the agent venv).

Run:
    python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")

try:
    import yaml

    _spec = importlib.util.spec_from_file_location("build_tools", os.path.join(_ROOT, "scripts", "build_tools.py"))
    bt = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(bt)
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

TABLES = {"claims": {"existing": "Claims"}}


@unittest.skipUnless(_YAML_AVAILABLE, "PyYAML not installed (run under the agent venv)")
class GeneratedFilesTests(unittest.TestCase):
    def test_generated_files_are_up_to_date(self):
        stale = [
            p.relative_to(bt.ROOT).as_posix()
            for p, text in bt.outputs().items()
            if not p.is_file() or p.read_text(encoding="utf-8") != text
        ]
        self.assertEqual(stale, [], "run: uv run --project app python scripts/build_tools.py")

    def test_every_gateway_target_has_a_tool(self):
        manifest = json.loads(bt.MANIFEST.read_text(encoding="utf-8"))
        agentcore = json.loads(bt.AGENTCORE_JSON.read_text(encoding="utf-8"))
        targets = {t["name"] for t in agentcore["agentCoreGateways"][0]["targets"]}
        self.assertEqual(targets, {t["target"] for t in manifest["tools"]})

    def test_default_use_case_matches_main_py(self):
        manifest = json.loads(bt.MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["default_use_case"], bt.default_use_case())


@unittest.skipUnless(_YAML_AVAILABLE, "PyYAML not installed (run under the agent venv)")
class BlueprintValidationTests(unittest.TestCase):
    def _tool(self, **fields):
        return {"name": "t", "description": "d", "table": "claims", **fields}

    def test_retrieval_schema_from_lookup_keys(self):
        fields, schema = bt._blueprint_tool(
            self._tool(blueprint="retrieval", lookup=[{"key": "claim_id", "description": "id"}]), "loc", TABLES
        )
        self.assertEqual(schema[0]["inputSchema"]["required"], ["claim_id"])
        self.assertEqual(fields["table_access"], "read")
        self.assertEqual(fields["tool_config"]["lookup"], [{"key": "claim_id"}])

    def test_retrieval_needs_lookup_or_filter(self):
        with self.assertRaisesRegex(bt.BuildError, "exactly one of lookup"):
            bt._blueprint_tool(self._tool(blueprint="retrieval"), "loc", TABLES)

    def test_unknown_table(self):
        with self.assertRaisesRegex(bt.BuildError, "not declared"):
            bt._blueprint_tool(self._tool(blueprint="retrieval", table="nope", lookup=[{"key": "k"}]), "loc", TABLES)

    def test_unknown_blueprint(self):
        with self.assertRaisesRegex(bt.BuildError, "blueprint must be one of"):
            bt._blueprint_tool(self._tool(blueprint="magic"), "loc", TABLES)

    def test_find_or_create_needs_identifying_fields(self):
        tool = self._tool(blueprint="record_write", inputs={"a": {"type": "string"}}, if_exists="return_existing")
        with self.assertRaisesRegex(bt.BuildError, "needs id.from"):
            bt._blueprint_tool(tool, "loc", TABLES)

    def test_id_fields_must_be_inputs(self):
        tool = self._tool(
            blueprint="record_write",
            inputs={"a": {"type": "string"}},
            id={"field": "x", "from": ["b"]},
            if_exists="return_existing",
        )
        with self.assertRaisesRegex(bt.BuildError, "not one of the inputs"):
            bt._blueprint_tool(tool, "loc", TABLES)

    def test_record_write_schema_from_inputs(self):
        tool = self._tool(
            blueprint="record_write", inputs={"a": {"type": "integer", "required": True, "description": "A"}}
        )
        fields, schema = bt._blueprint_tool(tool, "loc", TABLES)
        self.assertEqual(schema[0]["inputSchema"]["properties"]["a"], {"type": "integer", "description": "A"})
        self.assertEqual(schema[0]["inputSchema"]["required"], ["a"])
        self.assertEqual(fields["table_access"], "write")


@unittest.skipUnless(_YAML_AVAILABLE, "PyYAML not installed (run under the agent venv)")
class TableValidationTests(unittest.TestCase):
    def test_existing_must_be_a_shared_table(self):
        with self.assertRaisesRegex(bt.BuildError, "existing must be one of"):
            bt._resolve_tables("uc", {"tables": {"x": {"existing": "Nope"}}}, "where")

    def test_created_table_is_namespaced_by_use_case(self):
        refs, created = bt._resolve_tables(
            "hesta_agent", {"tables": {"members": {"partition_key": {"name": "member_id"}}}}, "where"
        )
        self.assertEqual(refs["members"], {"created": "hesta_agent.members"})
        self.assertEqual(created[0]["partition_key"], {"name": "member_id", "type": "string"})


@unittest.skipUnless(_YAML_AVAILABLE, "PyYAML not installed (run under the agent venv)")
class ListFromInputValidationTests(unittest.TestCase):
    def _tool(self, flt):
        return {"name": "t", "description": "d", "table": "claims", "blueprint": "retrieval", "filter": flt}

    def test_from_input_becomes_a_required_argument(self):
        fields, schema = bt._blueprint_tool(
            self._tool({"index": "i", "key": "customer_id", "from_input": True, "description": "who"}), "loc", TABLES
        )
        self.assertEqual(schema[0]["inputSchema"]["required"], ["customer_id"])
        self.assertNotIn("description", fields["tool_config"]["filter"])

    def test_where_fields_become_required_arguments(self):
        fields, schema = bt._blueprint_tool(
            self._tool({"index": "i", "key": "status", "value": "open", "where": {"owner": "whose"}}), "loc", TABLES
        )
        self.assertEqual(schema[0]["inputSchema"]["required"], ["owner"])
        self.assertEqual(fields["tool_config"]["filter"]["where"], ["owner"])

    def test_where_must_be_a_mapping(self):
        with self.assertRaisesRegex(bt.BuildError, "filter.where"):
            bt._blueprint_tool(self._tool({"index": "i", "key": "k", "value": "v", "where": ["owner"]}), "loc", TABLES)

    def test_value_and_from_input_are_exclusive(self):
        with self.assertRaisesRegex(bt.BuildError, "exactly one of value or from_input"):
            bt._blueprint_tool(self._tool({"index": "i", "key": "k", "value": "v", "from_input": True}), "loc", TABLES)

    def test_index_keys_are_normalised(self):
        _, created = bt._resolve_tables(
            "uc",
            {
                "tables": {
                    "t": {
                        "partition_key": {"name": "id"},
                        "indexes": [{"name": "ix", "partition_key": {"name": "c"}, "sort_key": {"name": "d"}}],
                    }
                }
            },
            "where",
        )
        self.assertEqual(created[0]["indexes"][0]["sort_key"], {"name": "d", "type": "string"})

    def test_index_needs_partition_key(self):
        with self.assertRaisesRegex(bt.BuildError, "partition_key"):
            bt._resolve_tables(
                "uc", {"tables": {"t": {"partition_key": {"name": "id"}, "indexes": [{"name": "ix"}]}}}, "w"
            )


if __name__ == "__main__":
    unittest.main()
