"""Tests for agent tools."""

import tempfile
import unittest
from claw.agent_tools import (
    default_tool_registry, execute_tool,
    AgentTool, ToolRegistry, ToolExecutionContext,
)


class TestToolRegistry(unittest.TestCase):
    def test_default_registry_has_8_tools(self):
        registry = default_tool_registry()
        tools = [t.name for t in registry.list_tools()]
        expected = ["list_dir", "read_file", "write_file", "edit_file",
                   "glob_search", "grep_search", "bash", "web_search",
                   "web_fetch", "use_skill"]
        for name in expected:
            self.assertIn(name, tools)
        # non_tool_call was removed to avoid confusing non-Claude models
        self.assertNotIn("non_tool_call", tools)

    def test_get_tool(self):
        registry = default_tool_registry()
        tool = registry.get("read_file")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "read_file")

    def test_execute_read_file(self):
        result = execute_tool("read_file", {"path": "/nonexistent/file.txt"})
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    def test_execute_unknown_tool(self):
        result = execute_tool("unknown_tool", {})
        self.assertFalse(result.ok)
        self.assertIn("Unknown tool", result.error)


class TestToolExecution(unittest.TestCase):
    def test_list_dir_nonexistent(self):
        result = execute_tool("list_dir", {"path": "/nonexistent/path"})
        self.assertFalse(result.ok)

    def test_failed_bash_preserves_exit_code_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            result = execute_tool(
                "bash",
                {
                    "command": (
                        "python -c \"import sys; print('visible-out'); "
                        "print('visible-err', file=sys.stderr); "
                        "raise SystemExit(7)\""
                    )
                },
                ToolExecutionContext(
                    cwd=directory,
                    permissions={"allow_shell": True},
                ),
            )
        self.assertFalse(result.ok)
        self.assertIn("code 7", result.error)
        self.assertIn("visible-out", result.error)
        self.assertIn("visible-err", result.error)
        self.assertEqual(result.result["returncode"], 7)

    def test_workspace_restriction_blocks_file_paths_outside_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            result = execute_tool(
                "read_file",
                {"path": "../outside.txt"},
                ToolExecutionContext(
                    cwd=directory,
                    permissions={"restrict_workspace": True},
                ),
            )
        self.assertFalse(result.ok)
        self.assertIn("outside the configured workspace", result.error)


if __name__ == "__main__":
    unittest.main()
