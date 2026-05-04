"""Tests for agent session."""

import unittest
import time
from src.agent_session import AgentSession


class TestAgentSession(unittest.TestCase):
    """Test AgentSession creation, message operations, and serialization."""

    def test_create_session_defaults(self):
        s = AgentSession(session_id="test-1")
        self.assertEqual(s.session_id, "test-1")
        self.assertEqual(s.messages, [])
        self.assertEqual(s.metadata, {})
        self.assertIsNotNone(s.created_at)
        self.assertIsNotNone(s.updated_at)
        self.assertIsNone(s.model)
        self.assertIsNone(s.stop_reason)
        self.assertIsNone(s.cwd)
        self.assertIsNone(s.name)

    def test_create_session_with_all_fields(self):
        now = time.time()
        s = AgentSession(
            session_id="full-1",
            messages=[{"role": "user", "content": "hi"}],
            metadata={"key": "value"},
            created_at=now - 100,
            updated_at=now,
            model="test-model",
            stop_reason="completed",
            cwd="/tmp/test",
            name="my session",
        )
        self.assertEqual(s.session_id, "full-1")
        self.assertEqual(len(s.messages), 1)
        self.assertEqual(s.metadata["key"], "value")
        self.assertEqual(s.model, "test-model")
        self.assertEqual(s.stop_reason, "completed")
        self.assertEqual(s.cwd, "/tmp/test")
        self.assertEqual(s.name, "my session")

    # --- Message operations ---

    def test_add_user_message(self):
        s = AgentSession(session_id="msg-test")
        s.add_user_message("hello world")
        self.assertEqual(len(s.messages), 1)
        self.assertEqual(s.messages[0]["role"], "user")
        self.assertEqual(s.messages[0]["content"], "hello world")

    def test_add_assistant_message_text_only(self):
        s = AgentSession(session_id="msg-test")
        s.add_assistant_message(content="hello back")
        self.assertEqual(len(s.messages), 1)
        self.assertEqual(s.messages[0]["role"], "assistant")
        self.assertEqual(s.messages[0]["content"], "hello back")
        self.assertNotIn("tool_calls", s.messages[0])

    def test_add_assistant_message_with_tool_calls(self):
        from src.agent_types import ToolCall
        s = AgentSession(session_id="msg-test")
        tc = ToolCall(id="call_1", name="bash", arguments='{"command": "ls"}')
        s.add_assistant_message(tool_calls=[tc])
        self.assertEqual(len(s.messages), 1)
        self.assertEqual(s.messages[0]["role"], "assistant")
        self.assertEqual(len(s.messages[0]["tool_calls"]), 1)
        self.assertEqual(s.messages[0]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(s.messages[0]["tool_calls"][0]["name"], "bash")

    def test_add_tool_message(self):
        s = AgentSession(session_id="msg-test")
        s.add_tool_message(tool_call_id="call_1", content="file list output", tool_name="bash")
        self.assertEqual(len(s.messages), 1)
        self.assertEqual(s.messages[0]["role"], "tool")
        self.assertEqual(s.messages[0]["tool_call_id"], "call_1")
        self.assertEqual(s.messages[0]["tool_name"], "bash")

    def test_add_tool_message_without_name(self):
        s = AgentSession(session_id="msg-test")
        s.add_tool_message(tool_call_id="call_2", content="result")
        self.assertNotIn("tool_name", s.messages[0])

    def test_add_system_message(self):
        s = AgentSession(session_id="msg-test")
        s.add_system_message("system prompt")
        self.assertEqual(s.messages[0]["role"], "system")
        self.assertEqual(s.messages[0]["content"], "system prompt")

    def test_get_messages_returns_shallow_copy(self):
        """get_messages() returns a shallow copy — outer list is new, dicts are shared."""
        s = AgentSession(session_id="msg-test")
        s.add_user_message("hi")
        msgs = s.get_messages()
        # Outer list is a copy — appending doesn't affect original
        msgs.append({"role": "extra"})
        self.assertEqual(len(s.messages), 1)
        # But inner dicts are shared references
        self.assertIs(msgs[0], s.messages[0])

    def test_conversation_flow(self):
        """Simulate a full conversation: user -> assistant(tool) -> tool result -> assistant."""
        from src.agent_types import ToolCall
        s = AgentSession(session_id="flow-1")

        s.add_user_message("list files")
        tc = ToolCall(id="call_1", name="list_dir", arguments='{"path": "."}')
        s.add_assistant_message(tool_calls=[tc])
        s.add_tool_message(tool_call_id="call_1", content="file1.py, file2.py")
        s.add_assistant_message(content="Found 2 files: file1.py, file2.py")

        self.assertEqual(len(s.messages), 4)
        self.assertEqual(s.messages[0]["role"], "user")
        self.assertEqual(s.messages[1]["role"], "assistant")
        self.assertEqual(s.messages[2]["role"], "tool")
        self.assertEqual(s.messages[3]["role"], "assistant")
        self.assertEqual(s.messages[3]["content"], "Found 2 files: file1.py, file2.py")

    def test_updated_at_changes_on_message(self):
        s = AgentSession(session_id="time-test")
        t0 = s.updated_at
        time.sleep(0.01)
        s.add_user_message("hi")
        self.assertGreater(s.updated_at, t0)

    # --- Serialization ---

    def test_to_dict_and_from_dict_round_trip(self):
        s1 = AgentSession(
            session_id="ser-1",
            messages=[{"role": "user", "content": "hello"}],
            metadata={"version": 2},
            model="gpt-4",
            stop_reason="completed",
            cwd="/home/user/project",
            name="test session",
        )
        d = s1.to_dict()
        s2 = AgentSession.from_dict(d)
        self.assertEqual(s2.session_id, s1.session_id)
        self.assertEqual(len(s2.messages), len(s1.messages))
        self.assertEqual(s2.messages[0]["content"], "hello")
        self.assertEqual(s2.metadata, s1.metadata)
        self.assertEqual(s2.model, s1.model)
        self.assertEqual(s2.stop_reason, s1.stop_reason)
        self.assertEqual(s2.cwd, s1.cwd)
        self.assertEqual(s2.name, s1.name)

    def test_from_dict_minimal(self):
        s = AgentSession.from_dict({"session_id": "minimal"})
        self.assertEqual(s.session_id, "minimal")
        self.assertEqual(s.messages, [])
        self.assertEqual(s.metadata, {})
        self.assertIsNone(s.name)

    def test_from_dict_empty(self):
        s = AgentSession.from_dict({})
        self.assertEqual(s.session_id, "")
        self.assertEqual(s.messages, [])

    def test_to_dict_includes_name(self):
        s = AgentSession(session_id="named", name="my agent")
        d = s.to_dict()
        self.assertIn("name", d)
        self.assertEqual(d["name"], "my agent")

    def test_to_dict_includes_cwd(self):
        s = AgentSession(session_id="cwd-test", cwd="/path/to/project")
        d = s.to_dict()
        self.assertIn("cwd", d)
        self.assertEqual(d["cwd"], "/path/to/project")


if __name__ == "__main__":
    unittest.main()
