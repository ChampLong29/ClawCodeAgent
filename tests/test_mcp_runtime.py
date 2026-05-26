"""Tests for MCP runtime."""

import unittest
import tempfile
import os
import json
from claw.mcp_runtime import MCPRuntime, MCPConfig


class TestMCPRuntime(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_discover_mcp_config(self):
        config_path = os.path.join(self.tempdir, ".claw-mcp.json")
        with open(config_path, "w") as f:
            json.dump({
                "name": "test-mcp",
                "resources": [
                    {"uri": "file://README", "name": "readme"}
                ],
                "servers": []
            }, f)

        runtime = MCPRuntime(cwd=self.tempdir)
        state = runtime.get_state()
        self.assertIsNotNone(state)
        self.assertEqual(state["name"], "test-mcp")

    def test_no_config_returns_none(self):
        runtime = MCPRuntime(cwd=self.tempdir)
        state = runtime.get_state()
        self.assertIsNone(state)


if __name__ == "__main__":
    unittest.main()