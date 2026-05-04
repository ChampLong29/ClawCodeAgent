"""Tests for session store."""

import unittest
import tempfile
import os
from src.agent_session import AgentSession
from src.session_store import save_agent_session, load_agent_session, list_sessions


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_save_and_load_session(self):
        session = AgentSession(session_id="test-123")
        session.add_user_message("Hello")
        session.add_assistant_message("Hi there!")

        filepath = save_agent_session(session, self.tempdir)

        loaded = load_agent_session("test-123", self.tempdir)
        self.assertEqual(loaded.session_id, "test-123")
        self.assertEqual(len(loaded.messages), 2)

    def test_load_nonexistent_session(self):
        with self.assertRaises(FileNotFoundError):
            load_agent_session("nonexistent", self.tempdir)

    def test_list_sessions(self):
        session = AgentSession(session_id="test-list")
        save_agent_session(session, self.tempdir)

        sessions = list_sessions(self.tempdir)
        self.assertGreater(len(sessions), 0)


if __name__ == "__main__":
    unittest.main()