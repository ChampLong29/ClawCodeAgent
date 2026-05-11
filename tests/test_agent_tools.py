"""Tests for agent tools."""

import unittest
from claw.agent_tools import (
    default_tool_registry, execute_tool,
    AgentTool, ToolRegistry,
)


class TestToolRegistry(unittest.TestCase):
    def test_default_registry_has_8_tools(self):
        registry = default_tool_registry()
        tools = [t.name for t in registry.list_tools()]
        expected = ["list_dir", "read_file", "write_file", "edit_file",
                   "glob_search", "grep_search", "bash", "non_tool_call"]
        for name in expected:
            self.assertIn(name, tools)

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


if __name__ == "__main__":
    unittest.main()