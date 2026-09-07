"""Tests for task runtime."""

import tempfile
import unittest
from claw.task_runtime import TaskRuntime


class TestTaskRuntime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.tempdir = self.temporary.name

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_task(self):
        runtime = TaskRuntime(cwd=self.tempdir)
        task_id = runtime.create_task("Test task")
        self.assertIsNotNone(task_id)

    def test_get_state(self):
        runtime = TaskRuntime(cwd=self.tempdir)
        state = runtime.get_state()
        self.assertIn("tasks", state)
        self.assertIn("count", state)


if __name__ == "__main__":
    unittest.main()
