"""Pure parsing contracts for local Transformers tool-use inference."""

import json
import tempfile
import unittest
from pathlib import Path

from claw.transformers_client import TransformersToolClient, parse_qwen_tool_response


class QwenToolResponseTests(unittest.TestCase):
    def test_parses_native_qwen_tool_call_and_hides_thinking(self):
        content, calls = parse_qwen_tool_response(
            "<think>inspect first</think>\n"
            "<tool_call>\n"
            '{"name":"read_file","arguments":{"path":"src/app.py"}}\n'
            "</tool_call>"
        )
        self.assertEqual(content, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "read_file")
        self.assertEqual(
            json.loads(calls[0]["function"]["arguments"]),
            {"path": "src/app.py"},
        )

    def test_keeps_final_text_without_tool_tags(self):
        content, calls = parse_qwen_tool_response("Done. Tests pass.")
        self.assertEqual(content, "Done. Tests pass.")
        self.assertEqual(calls, [])

    def test_ignores_malformed_or_non_object_tool_arguments(self):
        content, calls = parse_qwen_tool_response(
            '<tool_call>{"name":"bash","arguments":"ls"}</tool_call>'
        )
        self.assertEqual(content, "")
        self.assertEqual(calls, [])

    def test_client_requires_existing_local_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaises(FileNotFoundError):
                TransformersToolClient(missing)


if __name__ == "__main__":
    unittest.main()
