"""Tests for DevFlow plan editing + Reviewer config + process/format rewards."""

import os
import tempfile
import unittest

from src.training.reviewer import ReviewerAgent


class TestPlanEditing(unittest.TestCase):
    """DevFlow step editing methods."""

    def setUp(self):
        from src.devflow_runtime import DevFlowRuntime, DevFlowStep
        self.tmpdir = tempfile.mkdtemp()
        self.rt = DevFlowRuntime(cwd=self.tmpdir)
        self.DevFlowStep = DevFlowStep

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup(self):
        self.rt.start_session("test goal")
        self.rt.session.steps = [
            self.DevFlowStep(id="step-1", title="Setup", goal="init"),
            self.DevFlowStep(id="step-2", title="Auth", goal="auth"),
            self.DevFlowStep(id="step-3", title="API", goal="crud"),
        ]
        self.rt.save()

    def test_edit_step_title(self):
        self._setup()
        ok = self.rt.edit_step("step-2", title="Authentication")
        self.assertTrue(ok)
        s = self._find("step-2")
        self.assertEqual(s.title, "Authentication")
        self.assertEqual(s.goal, "auth")  # unchanged

    def test_edit_step_multiple_fields(self):
        self._setup()
        ok = self.rt.edit_step("step-1", title="Init", goal="project init",
                               constraints="use poetry")
        self.assertTrue(ok)
        s = self._find("step-1")
        self.assertEqual(s.title, "Init")
        self.assertEqual(s.goal, "project init")
        self.assertEqual(s.constraints, "use poetry")

    def test_edit_nonexistent_step(self):
        self._setup()
        ok = self.rt.edit_step("step-99", title="X")
        self.assertFalse(ok)

    def test_remove_step(self):
        self._setup()
        ok = self.rt.remove_step("step-2")
        self.assertTrue(ok)
        ids = [s.id for s in self.rt.session.steps]
        self.assertEqual(ids, ["step-1", "step-3"])

    def test_remove_nonexistent(self):
        self._setup()
        ok = self.rt.remove_step("step-99")
        self.assertFalse(ok)

    def test_add_step(self):
        self._setup()
        ok = self.rt.add_step("New Step", goal="new", after_step_id="step-1")
        self.assertTrue(ok)
        ids = [s.id for s in self.rt.session.steps]
        self.assertEqual(ids[1], ids[1])  # has an ID
        self.assertEqual(len(self.rt.session.steps), 4)

    def test_add_step_at_end(self):
        self._setup()
        ok = self.rt.add_step("Last Step")
        self.assertTrue(ok)
        self.assertEqual(self.rt.session.steps[-1].title, "Last Step")

    def test_move_step(self):
        self._setup()
        ok = self.rt.move_step("step-3", "step-1")
        self.assertTrue(ok)
        ids = [s.id for s in self.rt.session.steps]
        self.assertEqual(ids, ["step-3", "step-1", "step-2"])

    def _find(self, step_id):
        for s in self.rt.session.steps:
            if s.id == step_id:
                return s
        return None


class TestReviewerConfig(unittest.TestCase):
    """Reviewer configuration via LifecycleRuntime."""

    def setUp(self):
        from src.lifecycle_runtime import LifecycleRuntime
        self.tmpdir = tempfile.mkdtemp()
        self.rt = LifecycleRuntime(cwd=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_default_enabled(self):
        self.assertTrue(self.rt.is_reviewer_enabled())

    def test_set_enabled(self):
        self.rt.set_reviewer_enabled(False)
        self.assertFalse(self.rt.is_reviewer_enabled())

    def test_enabled_for_phase(self):
        self.assertTrue(self.rt.is_reviewer_enabled_for("IMPLEMENTATION"))
        self.assertFalse(self.rt.is_reviewer_enabled_for("REQUIREMENTS"))

    def test_get_config(self):
        cfg = self.rt.get_reviewer_config()
        self.assertTrue(cfg["enabled"])
        self.assertIn("IMPLEMENTATION", cfg["auto_review_phases"])

    def test_disabled_disables_all_phases(self):
        self.rt.set_reviewer_enabled(False)
        self.assertFalse(self.rt.is_reviewer_enabled_for("IMPLEMENTATION"))


class TestProcessFormatRewards(unittest.TestCase):
    """Process and format reward computation."""

    def test_process_reward_perfect(self):
        trace = {
            "REQUIREMENTS": {"output_length": 500},
            "ARCHITECTURE": {"status": "completed"},
            "UNIT_TEST": {"status": "completed"},
            "CODE_REVIEW": {"issue_count": 3},
        }
        score = ReviewerAgent.compute_process_reward(trace)
        self.assertAlmostEqual(score, 1.0)

    def test_process_reward_partial(self):
        # 2 of 4 checks pass
        trace = {
            "REQUIREMENTS": {"output_length": 500},          # pass
            "ARCHITECTURE": {"status": "completed"},         # pass
            "UNIT_TEST": {"status": "pending"},              # fail
            "CODE_REVIEW": {"issue_count": 0},               # fail
        }
        score = ReviewerAgent.compute_process_reward(trace)
        self.assertAlmostEqual(score, 0.50)  # 2/4

    def test_process_reward_empty(self):
        score = ReviewerAgent.compute_process_reward({})
        self.assertAlmostEqual(score, 0.5)

    def test_format_reward_structured(self):
        outputs = {
            "REQUIREMENTS": "## Requirements\n\n- item 1\n- item 2\n\nMore text here " + "x" * 200,
        }
        score = ReviewerAgent.compute_format_reward(outputs)
        self.assertGreater(score, 0.7)

    def test_format_reward_unstructured(self):
        outputs = {
            "REQUIREMENTS": "just some plain text without any structure at all",
        }
        score = ReviewerAgent.compute_format_reward(outputs)
        self.assertLess(score, 0.5)

    def test_format_reward_empty(self):
        score = ReviewerAgent.compute_format_reward({})
        self.assertAlmostEqual(score, 0.0)

    def test_combined_with_all_five(self):
        review = type('obj', (object,), {"overall_score": 0.8})()
        reward = ReviewerAgent.combined_reward(
            test_pass_rate=1.0, diff_accuracy=0.9, review=review,
            process_score=0.8, format_score=0.7,
        )
        self.assertGreaterEqual(reward, 0.8)
        self.assertLessEqual(reward, 1.0)


if __name__ == "__main__":
    unittest.main()
