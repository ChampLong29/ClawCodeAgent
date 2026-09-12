"""Tests for append-only tree sessions and compaction metadata."""

import json
import os
import tempfile
import unittest

from claw.agent_session import AgentSession
from claw.session_store import load_agent_session, save_agent_session


class TestSessionTree(unittest.TestCase):
    def test_navigation_creates_branch_without_losing_descendants(self):
        session = AgentSession("tree")
        session.add_user_message("root")
        root_id = session.current_entry_id
        session.add_assistant_message("original")
        original_id = session.current_entry_id

        session.navigate_to(root_id)
        session.add_assistant_message("alternate")

        self.assertEqual([m["content"] for m in session.messages], ["root", "alternate"])
        self.assertEqual(len(session.entries), 3)
        self.assertEqual(
            [m["content"] for m in session.get_branch_messages(original_id)],
            ["root", "original"],
        )

    def test_first_kept_entry_ignores_runtime_only_messages(self):
        session = AgentSession("mapping")
        session.add_user_message("one")
        session.add_assistant_message("two")
        two_id = session.current_entry_id
        session.add_user_message("three")

        first = session.first_active_entry_id_for_messages(
            [
                {"role": "user", "content": "runtime-only notice"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
            ]
        )
        self.assertEqual(first, two_id)

    def test_tree_and_compaction_round_trip_jsonl(self):
        session = AgentSession("tree-save", name="branch demo")
        session.add_user_message("root")
        root_id = session.current_entry_id
        session.add_assistant_message("original")
        original_id = session.current_entry_id
        session.navigate_to(root_id)
        session.add_assistant_message("alternate")
        session.append_compaction(
            "summary",
            first_kept_entry_id=root_id,
            tokens_before=123,
            details={"file_edits": ["src/a.py"]},
        )
        session.label_entry(root_id, "branch-point")

        with tempfile.TemporaryDirectory() as tmp:
            save_agent_session(session, tmp)
            loaded = load_agent_session("tree-save", tmp)

        self.assertEqual(len(loaded.entries), 4)
        self.assertEqual([m["content"] for m in loaded.messages], ["root", "alternate"])
        self.assertEqual(
            [m["content"] for m in loaded.get_branch_messages(original_id)],
            ["root", "original"],
        )
        self.assertEqual(loaded.get_entry(root_id).label, "branch-point")
        compaction = loaded.entries[-1]
        self.assertEqual(compaction.entry_type, "compaction")
        self.assertEqual(compaction.payload["tokens_before"], 123)

    def test_legacy_jsonl_is_rewritten_with_stable_tree_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_dir = os.path.join(tmp, ".port_sessions", "agent")
            os.makedirs(sessions_dir)
            path = os.path.join(sessions_dir, "legacy.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"__meta__": True, "session_id": "legacy"}) + "\n")
                handle.write(json.dumps({"role": "user", "content": "old"}) + "\n")

            loaded = load_agent_session("legacy", tmp)
            entry_id = loaded.current_entry_id
            save_agent_session(loaded, tmp)
            reloaded = load_agent_session("legacy", tmp)

        self.assertEqual(reloaded.current_entry_id, entry_id)
        self.assertEqual(reloaded.messages[0]["content"], "old")


if __name__ == "__main__":
    unittest.main()
