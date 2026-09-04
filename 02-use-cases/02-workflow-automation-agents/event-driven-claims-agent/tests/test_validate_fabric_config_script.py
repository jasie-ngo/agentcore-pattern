"""CI/CD guardrail validation gate script (ADR-0015 decision 7)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "hesta-claimsagent"))

import validate_fabric_config  # noqa: E402

_GOOD_YAML = """
agents:
  writer:
    guarded: true
    role: member_facing_writer
workflow:
  start: writer
  nodes:
    - {id: writer, type: agent, implementation: writer}
  edges: []
"""

_BAD_YAML = """
agents:
  writer:
    guarded: false
    role: member_facing_writer
workflow:
  start: writer
  nodes:
    - {id: writer, type: agent, implementation: writer}
  edges: []
"""


_BOGUS_ROUTER_YAML = """
agents:
  writer:
    guarded: true
    role: member_facing_writer
workflow:
  start: writer
  nodes:
    - {id: writer, type: agent, implementation: writer}
    - {id: hitl_record, type: deterministic, implementation: hitl_record}
  edges:
    - {source: writer, target: hitl_record, router: is_status_qeury}
"""

_BOGUS_IMPLEMENTATION_YAML = """
agents:
  writer:
    guarded: true
    role: member_facing_writer
workflow:
  start: writer
  nodes:
    - {id: writer, type: agent, implementation: writer}
    - {id: hitl_record, type: deterministic, implementation: hitl_recrod}
  edges:
    - {source: writer, target: hitl_record}
"""

_UNGUARDED_UNTAGGED_WRITER_YAML = """
agents:
  writer: {}
workflow:
  start: writer
  nodes:
    - {id: writer, type: agent, implementation: writer}
  edges: []
"""


class ValidateFabricConfigScriptTests(unittest.TestCase):
    def _write(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_valid_config_returns_zero(self):
        self.assertEqual(validate_fabric_config.main(["prog", self._write(_GOOD_YAML)]), 0)

    def test_missing_guardrail_returns_nonzero(self):
        self.assertEqual(validate_fabric_config.main(["prog", self._write(_BAD_YAML)]), 1)

    def test_missing_argument_returns_two(self):
        self.assertEqual(validate_fabric_config.main(["prog"]), 2)

    def test_unregistered_router_returns_nonzero(self):
        self.assertEqual(validate_fabric_config.main(["prog", self._write(_BOGUS_ROUTER_YAML)]), 1)

    def test_unregistered_node_implementation_returns_nonzero(self):
        self.assertEqual(validate_fabric_config.main(["prog", self._write(_BOGUS_IMPLEMENTATION_YAML)]), 1)

    def test_untagged_member_facing_writer_returns_nonzero(self):
        """Identity backstop (ADR-0015 decision 7): an agent named ``writer`` with no
        ``role`` and no ``guarded`` must not sneak past the CI gate."""
        self.assertEqual(validate_fabric_config.main(["prog", self._write(_UNGUARDED_UNTAGGED_WRITER_YAML)]), 1)


if __name__ == "__main__":
    unittest.main()
