"""Tests for agent tools."""

import tempfile
import unittest
from pathlib import Path
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
    def test_read_file_is_bounded_and_reports_next_page_before_content(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "large.txt"
            source.write_text(
                "\n".join(f"line-{index}" for index in range(130)) + "\n",
                encoding="utf-8",
            )
            first = execute_tool(
                "read_file",
                {"path": "large.txt"},
                ToolExecutionContext(cwd=directory),
            )
            second = execute_tool(
                "read_file",
                {"path": "large.txt", "offset": 120, "limit": 20},
                ToolExecutionContext(cwd=directory),
            )

        self.assertTrue(first.ok)
        self.assertEqual(first.result["returned_lines"], 120)
        self.assertEqual(first.result["total_lines"], 130)
        self.assertTrue(first.result["truncated"])
        self.assertEqual(first.result["next_offset"], 120)
        self.assertEqual(second.result["returned_lines"], 10)
        self.assertFalse(second.result["truncated"])
        self.assertIsNone(second.result["next_offset"])
        self.assertIn("line-129", second.result["content"])

    def test_read_file_rejects_unbounded_page_size(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "source.py").write_text("x\n", encoding="utf-8")
            result = execute_tool(
                "read_file",
                {"path": "source.py", "limit": 501},
                ToolExecutionContext(cwd=directory),
            )
        self.assertFalse(result.ok)
        self.assertIn("limit", result.error)

    def test_code_outline_query_finds_late_symbol_without_full_outline(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "large_module.py"
            source.write_text(
                "\n".join(
                    [f"def helper_{index}():\n    pass" for index in range(180)]
                    + [
                        "class Dataset:",
                        "    def to_json_dict(self):",
                        "        \"\"\"Return JSON.\"\"\"",
                        "        return {}",
                    ]
                ),
                encoding="utf-8",
            )

            result = execute_tool(
                "code_outline",
                {
                    "path": "large_module.py",
                    "query": "to_json_dict",
                    "includeDocstrings": True,
                },
                ToolExecutionContext(cwd=directory),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.result["path"], "large_module.py")
        self.assertTrue(result.result["filtered"])
        self.assertEqual(result.result["query"], "to_json_dict")
        self.assertIn("to_json_dict", result.result["outline"])
        self.assertNotIn("helper_0", result.result["outline"])

    def test_grep_search_returns_bounded_relative_context_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package").mkdir()
            (root / "package" / "a.py").write_text(
                "before_a\nTARGET one\nafter_a\nTARGET two\n",
                encoding="utf-8",
            )
            (root / "package" / "b.py").write_text(
                "before_b\nTARGET three\nafter_b\n",
                encoding="utf-8",
            )
            (root / "package" / "ignored.txt").write_text(
                "TARGET ignored\n",
                encoding="utf-8",
            )
            context = ToolExecutionContext(cwd=directory)

            first = execute_tool(
                "grep_search",
                {
                    "pattern": "TARGET",
                    "path": "package",
                    "recursive": True,
                    "file_pattern": "*.py",
                    "max_results": 2,
                    "context_lines": 1,
                },
                context,
            )
            second = execute_tool(
                "grep_search",
                {
                    "pattern": "TARGET",
                    "path": "package",
                    "recursive": True,
                    "file_pattern": "*.py",
                    "max_results": 2,
                    "offset": 2,
                },
                context,
            )

        self.assertTrue(first.ok)
        self.assertEqual(first.result["count"], 2)
        self.assertTrue(first.result["truncated"])
        self.assertEqual(first.result["next_offset"], 2)
        self.assertEqual(first.result["matches"][0]["file"], "package/a.py")
        self.assertEqual(first.result["matches"][0]["before"], ["before_a"])
        self.assertEqual(first.result["matches"][0]["after"], ["after_a"])
        self.assertTrue(second.ok)
        self.assertEqual(second.result["count"], 1)
        self.assertFalse(second.result["truncated"])
        self.assertEqual(second.result["matches"][0]["file"], "package/b.py")

    def test_grep_search_rejects_unbounded_page_size(self):
        with tempfile.TemporaryDirectory() as directory:
            result = execute_tool(
                "grep_search",
                {"pattern": "x", "max_results": 51},
                ToolExecutionContext(cwd=directory),
            )
        self.assertFalse(result.ok)
        self.assertIn("max_results", result.error)

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

    def test_write_file_rejects_path_outside_mutation_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            result = execute_tool(
                "write_file",
                {"path": "tests/test_target.py", "content": "changed\n"},
                ToolExecutionContext(
                    cwd=directory,
                    permissions={
                        "allow_write": True,
                        "restrict_workspace": True,
                        "allowed_write_paths": ["src/target.py"],
                    },
                ),
            )
            self.assertFalse((Path(directory) / "tests" / "test_target.py").exists())

        self.assertFalse(result.ok)
        self.assertIn("outside the configured mutation allowlist", result.error)

    def test_edit_file_accepts_matching_mutation_glob(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "src" / "target.py"
            source.parent.mkdir()
            source.write_text("old\n", encoding="utf-8")
            result = execute_tool(
                "edit_file",
                {
                    "path": "src/target.py",
                    "old_string": "old",
                    "new_string": "new",
                },
                ToolExecutionContext(
                    cwd=directory,
                    permissions={
                        "allow_write": True,
                        "restrict_workspace": True,
                        "allowed_write_paths": ["src/**"],
                    },
                ),
            )
            self.assertTrue(result.ok)
            self.assertEqual(source.read_text(encoding="utf-8"), "new\n")

    def test_bash_fails_closed_when_write_allowlist_has_no_disposable_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            result = execute_tool(
                "bash",
                {"command": "echo changed > tests/test_target.py"},
                ToolExecutionContext(
                    cwd=directory,
                    permissions={
                        "allow_shell": True,
                        "allowed_write_paths": ["src/target.py"],
                    },
                ),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.result["error_type"], "unsafe_shell_workspace")



if __name__ == "__main__":
    unittest.main()
