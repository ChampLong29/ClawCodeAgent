"""Tests for team runtime."""

import unittest
import tempfile
import os
import json
from src.team_runtime import TeamRuntime


class TestTeamRuntime(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_discover_teams_config(self):
        config_path = os.path.join(self.tempdir, ".claw-teams.json")
        with open(config_path, "w") as f:
            json.dump({
                "teams": [
                    {"name": "platform", "members": ["alice", "bob"]}
                ]
            }, f)

        runtime = TeamRuntime(cwd=self.tempdir)
        state = runtime.get_state()
        self.assertEqual(state["count"], 1)

    def test_discover_claude_teams_json(self):
        # Create .claude directory
        claude_dir = os.path.join(self.tempdir, ".claude")
        os.makedirs(claude_dir)

        config_path = os.path.join(claude_dir, "teams.json")
        with open(config_path, "w") as f:
            json.dump({
                "teams": [
                    {"name": "qa", "members": ["charlie"]}
                ]
            }, f)

        runtime = TeamRuntime(cwd=self.tempdir)
        state = runtime.get_state()
        self.assertEqual(state["count"], 1)


if __name__ == "__main__":
    unittest.main()