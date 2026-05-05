"""Tests for rollback mechanism in Lifecycle and DevFlow runtimes."""

import os
import tempfile
import unittest

from src.agent_session import AgentSession
from src.lifecycle_runtime import LifecycleRuntime


class TestLifecycleRollback(unittest.TestCase):
    """LifecycleRuntime snapshot and rollback."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rt = LifecycleRuntime(cwd=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_start_session_first_phase_pending(self):
        session = self.rt.start_session("test goal")
        phase = session.get_current_phase()
        self.assertEqual(phase.name, "REQUIREMENTS")

    def test_rollback_targets_empty_at_start(self):
        self.rt.start_session("test goal")
        targets = self.rt.list_rollback_targets()
        self.assertEqual(targets, [])  # no completed phases yet

    def test_advance_then_rollback(self):
        self.rt.start_session("test goal")
        phase = self.rt.session.get_current_phase()
        phase.status = "in_progress"
        phase.output = "requirements document content"

        # Advance past REQUIREMENTS
        self.rt.advance_phase()
        self.assertEqual(self.rt.session.get_current_phase().name, "SYSTEM_DESIGN")

        # Rollback targets should include REQUIREMENTS
        targets = self.rt.list_rollback_targets()
        self.assertGreaterEqual(len(targets), 1)
        self.assertEqual(targets[0]["name"], "REQUIREMENTS")

        # Perform rollback
        ok = self.rt.rollback_to_phase("REQUIREMENTS")
        self.assertTrue(ok)
        self.assertEqual(self.rt.session.get_current_phase().name, "REQUIREMENTS")

        # System design should be reset to pending
        for p in self.rt.session.phases:
            if p.name == "SYSTEM_DESIGN":
                self.assertEqual(p.status, "pending")
                self.assertIsNone(p.output)

    def test_rollback_to_nonexistent_phase(self):
        self.rt.start_session("test goal")
        ok = self.rt.rollback_to_phase("NONEXISTENT")
        self.assertFalse(ok)

    def test_rollback_with_agent_session(self):
        agent_session = AgentSession(session_id="agent-1")
        agent_session.add_system_message("you are helpful")
        agent_session.add_user_message("build app")
        agent_session.mark_phase_boundary("REQUIREMENTS")
        agent_session.add_assistant_message("requirements doc")
        agent_session.mark_phase_boundary("SYSTEM_DESIGN")
        agent_session.add_assistant_message("design doc")

        self.rt.start_session("test goal")
        phase = self.rt.session.get_current_phase()
        phase.status = "in_progress"
        phase.output = "requirements content"
        self.rt.advance_phase(agent_session=agent_session)

        # Should have a snapshot
        targets = self.rt.list_rollback_targets()
        self.assertTrue(any(t["snapshot_exists"] for t in targets))

        # Rollback with agent session restore
        ok = self.rt.rollback_to_phase("REQUIREMENTS", agent_session)
        self.assertTrue(ok)

        # Agent session should be restored to REQUIREMENTS boundary
        self.assertIn("REQUIREMENTS", agent_session.phase_boundaries)

    def test_advance_multiple_then_rollback_to_first(self):
        self.rt.start_session("test goal")

        # Complete REQUIREMENTS
        p = self.rt.session.get_current_phase()
        p.status = "in_progress"
        p.output = "req doc"
        self.rt.advance_phase()

        # Complete SYSTEM_DESIGN
        p = self.rt.session.get_current_phase()
        p.status = "in_progress"
        p.output = "design doc"
        self.rt.advance_phase()

        # Should be at ARCHITECTURE
        self.assertEqual(self.rt.session.get_current_phase().name, "ARCHITECTURE")

        # Rollback to REQUIREMENTS
        ok = self.rt.rollback_to_phase("REQUIREMENTS")
        self.assertTrue(ok)
        self.assertEqual(self.rt.session.get_current_phase().name, "REQUIREMENTS")

        # All phases after should be pending
        for p in self.rt.session.phases:
            if p.name in ("SYSTEM_DESIGN", "ARCHITECTURE"):
                self.assertEqual(p.status, "pending")


class TestDevFlowRollback(unittest.TestCase):
    """DevFlowRuntime rollback."""

    def setUp(self):
        from src.devflow_runtime import DevFlowRuntime, DevFlowStep
        self.tmpdir = tempfile.mkdtemp()
        self.rt = DevFlowRuntime(cwd=self.tmpdir)
        self.DevFlowStep = DevFlowStep

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup_with_steps(self):
        """Create a session with architecture and steps."""
        self.rt.start_session("test goal")
        self.rt.session.architecture = "use FastAPI + PostgreSQL"
        self.rt.session.steps = [
            self.DevFlowStep(
                id="step-1", title="setup project", goal="init",
                constraints="none", acceptance_criteria="project runs",
                status="verified",
            ),
            self.DevFlowStep(
                id="step-2", title="add models", goal="models",
                constraints="none", acceptance_criteria="tests pass",
                status="verified",
            ),
            self.DevFlowStep(
                id="step-3", title="add routes", goal="routes",
                constraints="none", acceptance_criteria="api works",
                status="pending",
            ),
        ]
        self.rt.session.current_step_index = 2  # at step-3
        self.rt.session.phase = "IMPLEMENTATION"
        self.rt.save()

    def test_rollback_to_step(self):
        self._setup_with_steps()
        ok = self.rt.rollback_to_step("step-1")
        self.assertTrue(ok)
        self.assertEqual(self.rt.session.current_step_index, 0)

        # step-1, step-2, step-3 should all be pending
        for s in self.rt.session.steps:
            self.assertEqual(s.status, "pending")

    def test_rollback_to_middle_step(self):
        self._setup_with_steps()
        ok = self.rt.rollback_to_step("step-2")
        self.assertTrue(ok)
        self.assertEqual(self.rt.session.current_step_index, 1)

        # step-1 should still be verified
        self.assertEqual(self.rt.session.steps[0].status, "verified")
        # step-2 and step-3 should be pending
        self.assertEqual(self.rt.session.steps[1].status, "pending")
        self.assertEqual(self.rt.session.steps[2].status, "pending")

    def test_rollback_to_nonexistent_step(self):
        self._setup_with_steps()
        ok = self.rt.rollback_to_step("step-99")
        self.assertFalse(ok)

    def test_rollback_to_phase(self):
        self._setup_with_steps()
        # Currently at IMPLEMENTATION, roll back to ARCHITECTURE
        ok = self.rt.rollback_to_phase("ARCHITECTURE")
        self.assertTrue(ok)
        self.assertEqual(self.rt.session.phase, "ARCHITECTURE")
        self.assertIsNone(self.rt.session.architecture)

    def test_rollback_to_phase_cannot_roll_forward(self):
        self._setup_with_steps()
        self.rt.session.phase = "ARCHITECTURE"
        # Try to roll forward to IMPLEMENTATION
        ok = self.rt.rollback_to_phase("IMPLEMENTATION")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
