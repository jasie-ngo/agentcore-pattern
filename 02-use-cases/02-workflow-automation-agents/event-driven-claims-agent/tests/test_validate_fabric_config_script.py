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
    implementation: writer
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
    implementation: writer
    guarded: false
    role: member_facing_writer
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


if __name__ == "__main__":
    unittest.main()
