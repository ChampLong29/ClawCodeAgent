"""Tests for worktree runtime."""

import unittest
import tempfile
import os
from src.worktree_runtime import WorktreeRuntime


class TestWorktreeRuntime(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_get_state(self):
        runtime = WorktreeRuntime(cwd=self.tempdir)
        state = runtime.get_state()
        self.assertIn("worktrees", state)
        self.assertIn("state_path", state)


if __name__ == "__main__":
    unittest.main()