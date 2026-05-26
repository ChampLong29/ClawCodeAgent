"""Tests for session store."""

import unittest
import tempfile
import os
from claw.agent_session import AgentSession
from claw.session_store import (
    save_agent_session,
    load_agent_session,
    list_sessions,
    delete_agent_session,
    load_session_by_name,
    list_sessions_by_prefix,
)


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

    # --- Name field ---

    def test_save_and_load_session_with_name(self):
        s = AgentSession(session_id="named-1", name="my agent session")
        s.add_user_message("hi")
        save_agent_session(s, self.tempdir)

        loaded = load_agent_session("named-1", self.tempdir)
        self.assertEqual(loaded.session_id, "named-1")
        self.assertEqual(loaded.name, "my agent session")

    def test_list_sessions_includes_name(self):
        s = AgentSession(session_id="has-name", name="named session")
        save_agent_session(s, self.tempdir)

        sessions = list_sessions(self.tempdir)
        named = [s for s in sessions if s["session_id"] == "has-name"]
        self.assertEqual(len(named), 1)
        self.assertEqual(named[0]["name"], "named session")

    # --- List sessions by prefix ---

    def test_list_sessions_by_prefix(self):
        s1 = AgentSession(session_id="s1", name="train-ep1-task1")
        s2 = AgentSession(session_id="s2", name="train-ep1-task2")
        s3 = AgentSession(session_id="s3", name="other-session")
        save_agent_session(s1, self.tempdir)
        save_agent_session(s2, self.tempdir)
        save_agent_session(s3, self.tempdir)

        results = list_sessions_by_prefix("train-ep1", self.tempdir)
        self.assertEqual(len(results), 2)
        ids = {r["session_id"] for r in results}
        self.assertIn("s1", ids)
        self.assertIn("s2", ids)
        self.assertNotIn("s3", ids)

    def test_list_sessions_by_prefix_none(self):
        s = AgentSession(session_id="s1", name="abc")
        save_agent_session(s, self.tempdir)

        results = list_sessions_by_prefix("xyz", self.tempdir)
        self.assertEqual(len(results), 0)

    def test_list_sessions_by_prefix_no_dir(self):
        results = list_sessions_by_prefix("train", "/nonexistent/path")
        self.assertEqual(results, [])

    # --- Load by name ---

    def test_load_session_by_name(self):
        s = AgentSession(session_id="by-name-1", name="unique-test-name")
        s.add_user_message("hello")
        save_agent_session(s, self.tempdir)

        loaded = load_session_by_name("unique-test-name", self.tempdir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.session_id, "by-name-1")

    def test_load_session_by_name_not_found(self):
        result = load_session_by_name("no-such-name", self.tempdir)
        self.assertIsNone(result)

    def test_load_session_by_name_no_dir(self):
        result = load_session_by_name("whatever", "/nonexistent/path")
        self.assertIsNone(result)

    # --- Delete ---

    def test_delete_session(self):
        s = AgentSession(session_id="to-delete")
        save_agent_session(s, self.tempdir)

        result = delete_agent_session("to-delete", self.tempdir)
        self.assertTrue(result)

        with self.assertRaises(FileNotFoundError):
            load_agent_session("to-delete", self.tempdir)

    def test_delete_nonexistent_session(self):
        result = delete_agent_session("no-such", self.tempdir)
        self.assertFalse(result)

    # --- Session metadata in listing ---

    def test_list_sessions_enriched_fields(self):
        s = AgentSession(
            session_id="enriched-1",
            model="test-model-v1",
            stop_reason="completed",
            cwd="/tmp/work",
            name="enriched",
        )
        s.add_user_message("p1")
        s.add_assistant_message("r1")
        save_agent_session(s, self.tempdir)

        sessions = list_sessions(self.tempdir)
        found = [x for x in sessions if x["session_id"] == "enriched-1"]
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual(f["model"], "test-model-v1")
        self.assertEqual(f["stop_reason"], "completed")
        self.assertEqual(f["cwd"], "/tmp/work")
        self.assertEqual(f["name"], "enriched")
        self.assertEqual(f["message_count"], 2)

    def test_list_sessions_empty_dir(self):
        sessions = list_sessions(self.tempdir)
        self.assertEqual(sessions, [])

    def test_list_sessions_ignores_invalid_json(self):
        """Sessions dir with invalid JSON files should not crash listing."""
        sessions_dir = os.path.join(self.tempdir, ".port_sessions", "agent")
        os.makedirs(sessions_dir, exist_ok=True)
        with open(os.path.join(sessions_dir, "bad.json"), "w") as f:
            f.write("not valid json {{{")

        sessions = list_sessions(self.tempdir)
        self.assertIsInstance(sessions, list)

    def test_save_preserves_all_fields(self):
        s = AgentSession(
            session_id="full-save",
            model="claude-4",
            stop_reason="stopped",
            cwd="/project",
            name="important work",
        )
        s.add_user_message("hi")
        save_agent_session(s, self.tempdir)

        loaded = load_agent_session("full-save", self.tempdir)
        self.assertEqual(loaded.model, "claude-4")
        self.assertEqual(loaded.stop_reason, "stopped")
        self.assertEqual(loaded.cwd, "/project")
        self.assertEqual(loaded.name, "important work")
        self.assertEqual(len(loaded.messages), 1)


if __name__ == "__main__":
    unittest.main()
