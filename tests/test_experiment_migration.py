"""Compatibility tests for legacy task and evaluation records."""

import unittest

from claw.experiment.migration import (
    coding_task_to_spec,
    rollout_result_to_verification,
)
from claw.training.runner import RolloutResult
from claw.training.tasks import CodingTask


class TestLegacyCompatibility(unittest.TestCase):
    def test_coding_task_converts_to_versioned_spec(self):
        task = CodingTask(
            id="task-1",
            prompt="Fix it",
            type="fix_bug",
            test_commands=["python -m unittest"],
            ground_truth_files={"main.py": "print('fixed')\n"},
        )
        spec = coding_task_to_spec(
            task,
            domain="python-cli",
            split="dev",
            license_name="MIT",
        )
        self.assertEqual(spec.schema_version, "coding_task.v2")
        self.assertEqual(spec.task_id, "task-1")
        self.assertEqual(spec.split, "dev")
        self.assertEqual(len(spec.content_hash), 64)

    def test_rollout_converts_to_separate_fact_and_evaluation_records(self):
        rollout = RolloutResult(
            task_id="task-1",
            session_id="session-1",
            stop_reason="completed",
            reward=1.0,
            messages=[{"role": "assistant", "content": "done"}],
            usage={"output_tokens": 1},
            test_result={"passed_tests": 1, "total_tests": 1},
            diff_result={"matches": 1, "total": 1},
        )
        trajectory = rollout.to_trajectory()
        report = rollout.to_verification(
            trajectory_ref=trajectory.header.trajectory_id
        )
        self.assertEqual(report.verdict, "success")
        self.assertTrue(report.hard_gate_passed)
        self.assertEqual(report.aggregate_score, 1.0)
        self.assertEqual(len(report.signals), 2)
        self.assertTrue(all(signal.status == "pass" for signal in report.signals))
        self.assertNotIn("reward", str(trajectory.to_dict()))

    def test_missing_hard_signal_is_not_verifiable(self):
        rollout = {
            "task_id": "task-1",
            "session_id": "session-1",
            "stop_reason": "completed",
            "reward": 0.99,
            "messages": [],
        }
        report = rollout_result_to_verification(
            rollout, trajectory_ref="trajectory-1"
        )
        self.assertEqual(report.verdict, "not_verifiable")
        self.assertFalse(report.hard_gate_passed)

    def test_soft_score_cannot_override_hard_failure(self):
        rollout = {
            "task_id": "task-1",
            "session_id": "session-1",
            "stop_reason": "completed",
            "reward": 0.99,
            "messages": [],
            "test_result": {"passed_tests": 0, "total_tests": 1},
            "review_report": {"overall_score": 1.0},
        }
        report = rollout_result_to_verification(
            rollout, trajectory_ref="trajectory-1"
        )
        self.assertEqual(report.verdict, "failure")
        self.assertFalse(report.hard_gate_passed)


if __name__ == "__main__":
    unittest.main()
