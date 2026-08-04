import unittest

from claw.agent_context import format_context_for_prompt


class AgentContextFormattingTests(unittest.TestCase):
    def test_environment_context_includes_exact_working_directory(self):
        rendered = format_context_for_prompt(
            {
                "cwd": "/episode/workspace",
                "git": {},
                "shell": {},
                "platform": {"system": "Linux"},
            }
        )
        self.assertIn("Working directory: /episode/workspace", rendered)


if __name__ == "__main__":
    unittest.main()
