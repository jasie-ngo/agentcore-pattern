"""fabric.loader: YAML -> FabricConfig, with validation applied (ADR-0015 decisions 1, 7)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

from fabric.loader import load_fabric_config  # noqa: E402
from fabric.schema import FabricConfigError  # noqa: E402

_VALID_YAML = """
agents:
  intent_identifier:
    implementation: intent_identifier
    fast: true
  writer:
    implementation: writer
    guarded: true
    role: member_facing_writer

workflow:
  start: intent_identifier
  nodes:
    - {id: intent_identifier, type: agent, implementation: intent_identifier}
    - {id: writer, type: agent, implementation: writer}
  edges:
    - {source: intent_identifier, target: writer}
"""

_MISSING_START_YAML = """
agents: {}
workflow:
  nodes: []
  edges: []
"""

_UNGUARDED_MEMBER_FACING_YAML = """
agents:
  writer:
    implementation: writer
    role: member_facing_writer
workflow:
  start: writer
  nodes:
    - {id: writer, type: agent, implementation: writer}
  edges: []
"""


class LoadFabricConfigTests(unittest.TestCase):
    def _write(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_loads_valid_config(self):
        config = load_fabric_config(self._write(_VALID_YAML))
        self.assertEqual(config.workflow.start, "intent_identifier")
        self.assertEqual({n.id for n in config.workflow.nodes}, {"intent_identifier", "writer"})
        self.assertTrue(config.agents["intent_identifier"].fast)
        self.assertTrue(config.agents["writer"].guarded)
        self.assertEqual(config.agents["writer"].role, "member_facing_writer")

    def test_missing_workflow_start_raises(self):
        with self.assertRaises(FabricConfigError):
            load_fabric_config(self._write(_MISSING_START_YAML))

    def test_unguarded_member_facing_role_raises(self):
        with self.assertRaises(FabricConfigError):
            load_fabric_config(self._write(_UNGUARDED_MEMBER_FACING_YAML))

    def test_non_mapping_top_level_raises(self):
        with self.assertRaises(FabricConfigError):
            load_fabric_config(self._write("- just\n- a\n- list\n"))


if __name__ == "__main__":
    unittest.main()
