"""Tests for DevFlow runtime."""

import unittest
import tempfile
import os
from claw.devflow_runtime import (
    DevFlowRuntime,
    DevFlowSession,
    DevFlowStep,
    DevFlowModule,
)


class TestDevFlowStep(unittest.TestCase):
    """Test DevFlowStep dataclass."""

    def test_create_step(self):
        s = DevFlowStep(
            id="step-1",
            title="Create User model",
            goal="Define User model with SQLAlchemy",
            constraints="Use UUID primary key",
            acceptance_criteria="Model imports without errors",
        )
        self.assertEqual(s.id, "step-1")
        self.assertEqual(s.title, "Create User model")
        self.assertEqual(s.status, "pending")
        self.assertEqual(s.depends_on, [])
        self.assertEqual(s.modules, [])

    def test_step_to_dict_and_from_dict(self):
        s = DevFlowStep(
            id="step-2",
            title="Implement auth",
            goal="Add JWT auth",
            status="in_progress",
            depends_on=["step-1"],
        )
        d = s.to_dict()
        self.assertEqual(d["id"], "step-2")
        self.assertEqual(d["depends_on"], ["step-1"])

        s2 = DevFlowStep.from_dict(d)
        self.assertEqual(s2.id, s.id)
        self.assertEqual(s2.title, s.title)
        self.assertEqual(s2.status, s.status)

    def test_has_modules_empty(self):
        s = DevFlowStep(id="s1", title="t1", goal="g1")
        self.assertFalse(s.has_modules())

    def test_has_modules_with_modules(self):
        s = DevFlowStep(id="s1", title="t1", goal="g1")
        s.modules = [
            DevFlowModule(id="m1", file_path="src/a.py", goal="g1", acceptance_criteria="ac1"),
        ]
        self.assertTrue(s.has_modules())

    def test_get_current_module(self):
        s = DevFlowStep(id="s1", title="t1", goal="g1")
        s.modules = [
            DevFlowModule(id="m1", file_path="src/a.py", goal="g1", acceptance_criteria="ac1"),
            DevFlowModule(id="m2", file_path="src/b.py", goal="g2", acceptance_criteria="ac2"),
        ]
        s.current_module_index = 0
        m = s.get_current_module()
        self.assertIsNotNone(m)
        self.assertEqual(m.id, "m1")

    def test_get_current_module_none(self):
        s = DevFlowStep(id="s1", title="t1", goal="g1")
        self.assertIsNone(s.get_current_module())

    def test_can_start_no_deps(self):
        s = DevFlowStep(id="s1", title="t1", goal="g1")
        self.assertTrue(s.can_start({}))

    def test_can_start_with_deps_resolved(self):
        s = DevFlowStep(id="s2", title="t2", goal="g2", depends_on=["s1"])
        all_steps = {"s1": DevFlowStep(id="s1", title="t1", goal="g1", status="verified")}
        self.assertTrue(s.can_start(all_steps))

    def test_can_start_with_deps_unresolved(self):
        s = DevFlowStep(id="s2", title="t2", goal="g2", depends_on=["s1"])
        all_steps = {"s1": DevFlowStep(id="s1", title="t1", goal="g1", status="pending")}
        self.assertFalse(s.can_start(all_steps))


class TestDevFlowModule(unittest.TestCase):
    """Test DevFlowModule dataclass."""

    def test_create_module(self):
        m = DevFlowModule(
            id="module-1",
            file_path="src/models/user.py",
            goal="Define User model",
            acceptance_criteria="Model imports OK",
        )
        self.assertEqual(m.id, "module-1")
        self.assertEqual(m.file_path, "src/models/user.py")
        self.assertEqual(m.status, "pending")

    def test_module_to_dict_and_from_dict(self):
        m = DevFlowModule(
            id="m1",
            file_path="src/app.py",
            goal="Create app",
            constraints="Use async",
            acceptance_criteria="App starts",
            status="implemented",
            implementation_result="Done",
        )
        d = m.to_dict()
        m2 = DevFlowModule.from_dict(d)
        self.assertEqual(m2.id, m.id)
        self.assertEqual(m2.file_path, m.file_path)
        self.assertEqual(m2.status, m.status)
        self.assertEqual(m2.implementation_result, "Done")


class TestDevFlowSession(unittest.TestCase):
    """Test DevFlowSession dataclass."""

    def test_create_session(self):
        s = DevFlowSession(session_id="dev-1", overall_goal="Build API")
        self.assertEqual(s.session_id, "dev-1")
        self.assertEqual(s.overall_goal, "Build API")
        self.assertEqual(s.phase, "INIT")
        self.assertEqual(s.steps, [])
        self.assertIsNone(s.architecture)
        self.assertEqual(s.current_step_index, 0)
        self.assertFalse(s.completed)

    def test_session_to_dict_and_from_dict(self):
        s = DevFlowSession(
            session_id="dev-2",
            overall_goal="Build app",
            user_constraints="Use Python 3.12",
            architecture="# Architecture doc",
            phase="ARCHITECTURE",
        )
        step = DevFlowStep(id="s1", title="Step 1", goal="goal 1", status="pending")
        s.steps = [step]

        d = s.to_dict()
        s2 = DevFlowSession.from_dict(d)
        self.assertEqual(s2.session_id, "dev-2")
        self.assertEqual(s2.overall_goal, "Build app")
        self.assertEqual(s2.user_constraints, "Use Python 3.12")
        self.assertEqual(s2.phase, "ARCHITECTURE")
        self.assertEqual(len(s2.steps), 1)
        self.assertEqual(s2.steps[0].title, "Step 1")

    def test_get_current_step(self):
        s = DevFlowSession(session_id="dev-3", overall_goal="g")
        s.steps = [
            DevFlowStep(id="s1", title="t1", goal="g1"),
            DevFlowStep(id="s2", title="t2", goal="g2"),
        ]
        s.current_step_index = 1
        step = s.get_current_step()
        self.assertIsNotNone(step)
        self.assertEqual(step.id, "s2")

    def test_get_current_step_empty(self):
        s = DevFlowSession(session_id="dev-4", overall_goal="g")
        self.assertIsNone(s.get_current_step())

    def test_get_step_by_id(self):
        s = DevFlowSession(session_id="dev-5", overall_goal="g")
        s.steps = [DevFlowStep(id="step-a", title="A", goal="ga")]
        found = s.get_step_by_id("step-a")
        self.assertIsNotNone(found)
        self.assertIsNone(s.get_step_by_id("step-x"))

    def test_progress_empty(self):
        s = DevFlowSession(session_id="dev-6", overall_goal="g")
        p = s.progress()
        self.assertEqual(p["total"], 0)
        self.assertEqual(p["percent"], 0)

    def test_progress_with_steps(self):
        s = DevFlowSession(session_id="dev-7", overall_goal="g")
        s.steps = [
            DevFlowStep(id="s1", title="t1", goal="g1", status="verified"),
            DevFlowStep(id="s2", title="t2", goal="g2", status="in_progress"),
            DevFlowStep(id="s3", title="t3", goal="g3", status="pending"),
        ]
        p = s.progress()
        self.assertEqual(p["total"], 3)
        self.assertEqual(p["verified"], 1)
        self.assertEqual(p["percent"], 33)


class TestDevFlowRuntime(unittest.TestCase):
    """Test DevFlowRuntime session management."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.rt = DevFlowRuntime(cwd=self.tempdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_get_session_none_initially(self):
        self.assertIsNone(self.rt.get_session())

    def test_start_session(self):
        session = self.rt.start_session("Build auth system", "Use JWT")
        self.assertIsNotNone(session)
        self.assertEqual(session.overall_goal, "Build auth system")
        self.assertEqual(session.user_constraints, "Use JWT")
        self.assertEqual(session.phase, "ARCHITECTURE")
        self.assertFalse(session.completed)

    def test_start_session_creates_session_id(self):
        session = self.rt.start_session("Test goal")
        self.assertIsNotNone(session.session_id)
        self.assertGreater(len(session.session_id), 4)  # formatted name + hash

    def test_has_active_session(self):
        self.assertFalse(self.rt.has_active_session())
        self.rt.start_session("Test")
        self.assertTrue(self.rt.has_active_session())

    def test_save_and_load_session(self):
        session = self.rt.start_session("Persist test")
        session.architecture = "# Design doc"
        self.rt.save()

        rt2 = DevFlowRuntime(cwd=self.tempdir)
        loaded = rt2.load(session.session_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.overall_goal, "Persist test")
        self.assertEqual(loaded.architecture, "# Design doc")

    def test_load_nonexistent_session(self):
        result = self.rt.load("no-such-id")
        self.assertIsNone(result)

    def test_list_sessions_empty(self):
        sessions = self.rt.list_sessions()
        self.assertEqual(sessions, [])

    def test_list_sessions(self):
        self.rt.start_session("Build API")
        self.rt.start_session("Build UI")
        sessions = self.rt.list_sessions()
        self.assertEqual(len(sessions), 2)

    def _setup_session_with_arch(self):
        """Helper: start session and set architecture so approve works."""
        self.rt.start_session("Test")
        session = self.rt.get_session()
        session.architecture = "# Test Architecture"
        self.rt.save()
        return session

    def test_approve_architecture(self):
        session = self._setup_session_with_arch()
        self.rt.approve_architecture()
        self.assertEqual(session.phase, "STEP_DEFINITION")

    def test_approve_steps(self):
        session = self._setup_session_with_arch()
        self.rt.approve_architecture()

        session.steps = [
            DevFlowStep(id="s1", title="Step 1", goal="g1"),
            DevFlowStep(id="s2", title="Step 2", goal="g2"),
        ]

        self.rt.approve_steps()
        self.assertEqual(session.phase, "STEP_ANALYSIS")

    def test_next_step(self):
        session = self._setup_session_with_arch()
        self.rt.approve_architecture()

        session.steps = [
            DevFlowStep(id="s1", title="Step 1", goal="g1", status="verified"),
            DevFlowStep(id="s2", title="Step 2", goal="g2", status="pending"),
        ]
        session.phase = "IMPLEMENTATION"
        session.current_step_index = 0

        has_next = self.rt.next_step()
        self.assertTrue(has_next)
        self.assertEqual(session.current_step_index, 1)

    def test_skip_step(self):
        session = self._setup_session_with_arch()
        self.rt.approve_architecture()

        session.steps = [
            DevFlowStep(id="s1", title="Step 1", goal="g1", status="verified"),
            DevFlowStep(id="s2", title="Step 2", goal="g2", status="pending"),
        ]
        session.phase = "IMPLEMENTATION"

        has_next = self.rt.skip_step()
        self.assertTrue(has_next)
        # skip_step marks as "failed" (not "skipped") per implementation
        self.assertEqual(session.steps[0].status, "failed")

    def test_retry_step(self):
        session = self._setup_session_with_arch()
        self.rt.approve_architecture()

        session.steps = [
            DevFlowStep(id="s1", title="Step 1", goal="g1", status="failed",
                        implementation_result="Error: bad code"),
        ]
        session.phase = "IMPLEMENTATION"

        self.rt.retry_step()
        self.assertEqual(session.steps[0].status, "pending")
        self.assertIsNone(session.steps[0].implementation_result)

    def test_get_state(self):
        self.rt.start_session("State test")
        state = self.rt.get_state()
        self.assertTrue(state["active"])
        self.assertIn("session", state)
        self.assertIn("progress", state)

    def test_get_state_no_session(self):
        state = self.rt.get_state()
        self.assertFalse(state["active"])

    def test_render_summary(self):
        self.rt.start_session("Summary test")
        summary = self.rt.render_summary()
        self.assertIn("DevFlow", summary)
        self.assertIn("ARCHITECTURE", summary)

    def test_render_summary_no_session(self):
        self.assertEqual(self.rt.render_summary(), "")

    def test_get_prompt_guidance(self):
        self.rt.start_session("Guidance test")
        guidance = self.rt.get_prompt_guidance()
        self.assertIn("ARCHITECTURE", guidance)
        self.assertIn("Guidance test", guidance)

    # --- Module operations ---

    def test_analyze_step_adds_modules(self):
        session = self._setup_session_with_arch()
        self.rt.approve_architecture()

        session.steps = [
            DevFlowStep(id="s1", title="Step 1", goal="g1",
                        constraints="c1", acceptance_criteria="ac1"),
        ]
        session.phase = "STEP_ANALYSIS"

        modules = [
            DevFlowModule(id="m1", file_path="src/a.py", goal="ga",
                          acceptance_criteria="aca"),
            DevFlowModule(id="m2", file_path="src/b.py", goal="gb",
                          acceptance_criteria="acb"),
        ]
        session.steps[0].modules = modules
        self.rt.approve_modules()
        self.assertEqual(session.phase, "IMPLEMENTATION")

    def test_next_module(self):
        session = self._setup_session_with_arch()
        self.rt.approve_architecture()

        modules = [
            DevFlowModule(id="m1", file_path="src/a.py", goal="ga",
                          acceptance_criteria="aca"),
            DevFlowModule(id="m2", file_path="src/b.py", goal="gb",
                          acceptance_criteria="acb"),
        ]
        step = DevFlowStep(id="s1", title="Step 1", goal="g1", modules=modules)
        step.current_module_index = 0
        session.steps = [step]
        session.phase = "IMPLEMENTATION"

        has_next = self.rt.next_module()
        self.assertTrue(has_next)
        self.assertEqual(step.current_module_index, 1)

    def test_next_module_last(self):
        session = self._setup_session_with_arch()
        self.rt.approve_architecture()

        module = DevFlowModule(id="m1", file_path="src/a.py", goal="ga",
                               acceptance_criteria="aca")
        step = DevFlowStep(id="s1", title="Step 1", goal="g1", modules=[module])
        step.current_module_index = 0
        session.steps = [step]
        session.phase = "IMPLEMENTATION"

        has_next = self.rt.next_module()
        self.assertFalse(has_next)  # Last module

    def test_archive(self):
        self.rt.start_session("Archive test")
        session = self.rt.get_session()
        session.architecture = "# My Architecture"
        session.steps = [
            DevFlowStep(id="s1", title="Step 1", goal="g1", status="verified",
                        implementation_result="Done"),
        ]
        session.phase = "DONE"
        session.completed = True

        path = self.rt.archive()
        self.assertTrue(os.path.exists(path))
        with open(path, "r") as f:
            content = f.read()
        self.assertIn("Archive test", content)
        self.assertIn("Step 1", content)


if __name__ == "__main__":
    unittest.main()
