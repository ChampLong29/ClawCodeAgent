"""Tests for lifecycle runtime."""

import unittest
import tempfile
import os
from claw.lifecycle_runtime import (
    LifecycleRuntime,
    LifecycleSession,
    LifecyclePhase,
    DEFAULT_LIFECYCLE_PHASES,
    DEVFLOW_PHASES,
    WRITE_PHASES,
    PHASE_SKILL_MAP,
)


class TestLifecyclePhase(unittest.TestCase):
    """Test LifecyclePhase dataclass."""

    def test_create_phase_defaults(self):
        p = LifecyclePhase(name="REQUIREMENTS")
        self.assertEqual(p.name, "REQUIREMENTS")
        self.assertEqual(p.status, "pending")
        self.assertIsNone(p.output)
        self.assertIsNone(p.artifact_path)

    def test_create_phase_with_fields(self):
        p = LifecyclePhase(
            name="IMPLEMENTATION",
            status="in_progress",
            output="Created models.py",
            artifact_path="docs/impl.md",
        )
        self.assertEqual(p.status, "in_progress")
        self.assertEqual(p.output, "Created models.py")

    def test_phase_to_dict_and_from_dict(self):
        p = LifecyclePhase(
            name="CODE_REVIEW",
            status="completed",
            output="All clear",
            artifact_path="docs/review.md",
        )
        d = p.to_dict()
        p2 = LifecyclePhase.from_dict(d)
        self.assertEqual(p2.name, p.name)
        self.assertEqual(p2.status, p.status)
        self.assertEqual(p2.output, p.output)
        self.assertEqual(p2.artifact_path, p.artifact_path)

    def test_phase_from_dict_minimal(self):
        p = LifecyclePhase.from_dict({"name": "REQUIREMENTS"})
        self.assertEqual(p.name, "REQUIREMENTS")
        self.assertEqual(p.status, "pending")
        self.assertIsNone(p.output)


class TestLifecycleSession(unittest.TestCase):
    """Test LifecycleSession dataclass."""

    def test_create_session(self):
        phases = [LifecyclePhase(name="REQUIREMENTS")]
        s = LifecycleSession(
            session_id="life-1",
            overall_goal="Build auth system",
            user_constraints="Use JWT",
            phases=phases,
        )
        self.assertEqual(s.session_id, "life-1")
        self.assertEqual(s.overall_goal, "Build auth system")
        self.assertEqual(len(s.phases), 1)
        self.assertFalse(s.completed)

    def test_get_current_phase(self):
        phases = [
            LifecyclePhase(name="REQUIREMENTS", status="completed"),
            LifecyclePhase(name="ARCHITECTURE", status="in_progress"),
        ]
        s = LifecycleSession(session_id="lc-1", overall_goal="g", phases=phases,
                             current_phase_index=1)
        phase = s.get_current_phase()
        self.assertIsNotNone(phase)
        self.assertEqual(phase.name, "ARCHITECTURE")

    def test_get_current_phase_out_of_bounds(self):
        s = LifecycleSession(session_id="lc-2", overall_goal="g",
                             phases=[], current_phase_index=0)
        self.assertIsNone(s.get_current_phase())

    def test_progress_empty(self):
        s = LifecycleSession(session_id="lc-3", overall_goal="g", phases=[])
        p = s.progress()
        self.assertEqual(p["total"], 0)
        self.assertEqual(p["percent"], 0)

    def test_progress_with_phases(self):
        phases = [
            LifecyclePhase(name="R1", status="completed"),
            LifecyclePhase(name="R2", status="completed"),
            LifecyclePhase(name="R3", status="in_progress"),
            LifecyclePhase(name="R4", status="pending"),
            LifecyclePhase(name="R5", status="skipped"),
        ]
        s = LifecycleSession(session_id="lc-4", overall_goal="g", phases=phases)
        p = s.progress()
        self.assertEqual(p["total"], 5)
        self.assertEqual(p["completed"], 2)
        self.assertEqual(p["in_progress"], 1)
        self.assertEqual(p["pending"], 1)
        self.assertEqual(p["skipped"], 1)
        self.assertEqual(p["percent"], 40)  # 2/5 = 40%

    def test_get_phase_by_name(self):
        phases = [LifecyclePhase(name="REQUIREMENTS", output="doc")]
        s = LifecycleSession(session_id="lc-5", overall_goal="g", phases=phases)
        self.assertIsNotNone(s.get_phase_by_name("REQUIREMENTS"))
        self.assertIsNone(s.get_phase_by_name("NONEXISTENT"))

    def test_get_completed_output(self):
        phases = [
            LifecyclePhase(name="REQUIREMENTS", status="completed",
                           output="Full requirements doc"),
        ]
        s = LifecycleSession(session_id="lc-6", overall_goal="g", phases=phases)
        output = s.get_completed_output("REQUIREMENTS")
        self.assertEqual(output, "Full requirements doc")

    def test_get_completed_output_not_completed(self):
        phases = [LifecyclePhase(name="REQUIREMENTS", status="pending")]
        s = LifecycleSession(session_id="lc-7", overall_goal="g", phases=phases)
        output = s.get_completed_output("REQUIREMENTS")
        self.assertEqual(output, "Not available.")

    def test_session_to_dict_and_from_dict(self):
        phases = [
            LifecyclePhase(name="REQUIREMENTS", status="completed",
                           output="req doc", artifact_path="docs/req.md"),
            LifecyclePhase(name="IMPLEMENTATION", status="in_progress"),
        ]
        s = LifecycleSession(
            session_id="life-ser",
            overall_goal="Build API",
            user_constraints="Python only",
            phases=phases,
            current_phase_index=1,
            devflow_session_id="devflow-123",
            completed=False,
        )
        d = s.to_dict()
        s2 = LifecycleSession.from_dict(d)
        self.assertEqual(s2.session_id, "life-ser")
        self.assertEqual(s2.overall_goal, "Build API")
        self.assertEqual(s2.user_constraints, "Python only")
        self.assertEqual(len(s2.phases), 2)
        self.assertEqual(s2.phases[0].name, "REQUIREMENTS")
        self.assertEqual(s2.phases[0].output, "req doc")
        self.assertEqual(s2.phases[0].artifact_path, "docs/req.md")
        self.assertEqual(s2.current_phase_index, 1)
        self.assertEqual(s2.devflow_session_id, "devflow-123")


class TestLifecycleRuntime(unittest.TestCase):
    """Test LifecycleRuntime session management."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.rt = LifecycleRuntime(cwd=self.tempdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    # --- Session lifecycle ---

    def test_get_session_none_initially(self):
        self.assertIsNone(self.rt.get_session())

    def test_start_session_default_phases(self):
        session = self.rt.start_session("Build auth system")
        self.assertIsNotNone(session)
        self.assertEqual(session.overall_goal, "Build auth system")
        self.assertEqual(len(session.phases), len(DEFAULT_LIFECYCLE_PHASES))
        # First phase should be active
        first = session.get_current_phase()
        self.assertIsNotNone(first)
        self.assertEqual(first.name, DEFAULT_LIFECYCLE_PHASES[0])

    def test_start_session_custom_phases(self):
        session = self.rt.start_session(
            "Test",
            phase_list=["REQUIREMENTS", "IMPLEMENTATION", "ACCEPTANCE"],
        )
        self.assertEqual(len(session.phases), 3)
        names = [p.name for p in session.phases]
        self.assertEqual(names, ["REQUIREMENTS", "IMPLEMENTATION", "ACCEPTANCE"])

    def test_start_session_with_constraints(self):
        session = self.rt.start_session("Goal", constraints="Use Python 3.12, no JS")
        self.assertEqual(session.user_constraints, "Use Python 3.12, no JS")

    def test_has_active_session(self):
        self.assertFalse(self.rt.has_active_session())
        self.rt.start_session("Test")
        self.assertTrue(self.rt.has_active_session())

    # --- Phase navigation ---

    def test_advance_phase(self):
        self.rt.start_session("Test")
        session = self.rt.get_session()

        phase = session.get_current_phase()
        phase.status = "in_progress"
        phase.output = "Done"

        has_next = self.rt.advance_phase()
        self.assertTrue(has_next)
        # Old phase marked completed
        self.assertEqual(session.phases[0].status, "completed")
        # New current phase
        new_phase = session.get_current_phase()
        self.assertEqual(new_phase.name, DEFAULT_LIFECYCLE_PHASES[1])

    def test_advance_phase_marks_completed(self):
        self.rt.start_session("Test")
        session = self.rt.get_session()

        for i in range(len(session.phases)):
            phase = session.get_current_phase()
            if phase is None:
                break
            phase.status = "in_progress"
            phase.output = f"Output for {phase.name}"
            self.rt.advance_phase()

        # All should be completed
        self.assertTrue(session.completed)
        for p in session.phases:
            self.assertEqual(p.status, "completed")

    def test_skip_phase(self):
        self.rt.start_session("Test")
        session = self.rt.get_session()

        first_name = session.get_current_phase().name
        has_next = self.rt.skip_phase()
        self.assertTrue(has_next)
        self.assertEqual(session.phases[0].status, "skipped")
        self.assertNotEqual(session.get_current_phase().name, first_name)

    def test_retry_phase(self):
        self.rt.start_session("Test")
        session = self.rt.get_session()

        phase = session.get_current_phase()
        phase.status = "failed"
        phase.output = "Bad result"

        self.rt.retry_phase()
        self.assertEqual(phase.status, "pending")
        self.assertIsNone(phase.output)

    # --- Persistence ---

    def test_save_and_load(self):
        session = self.rt.start_session("Save test")
        session_id = session.session_id

        phase = session.get_current_phase()
        phase.status = "in_progress"
        phase.output = "Some output"
        self.rt.save()

        rt2 = LifecycleRuntime(cwd=self.tempdir)
        loaded = rt2.load(session_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.session_id, session_id)
        self.assertEqual(loaded.overall_goal, "Save test")
        self.assertEqual(loaded.get_current_phase().name,
                         session.get_current_phase().name)

    def test_load_nonexistent(self):
        self.assertIsNone(self.rt.load("no-such"))

    def test_list_sessions_empty(self):
        self.assertEqual(self.rt.list_sessions(), [])

    def test_list_sessions(self):
        self.rt.start_session("Project A")
        self.rt.start_session("Project B")
        sessions = self.rt.list_sessions()
        self.assertEqual(len(sessions), 2)

    # --- Archive ---

    def test_archive(self):
        self.rt.start_session("Archive me")
        session = self.rt.get_session()
        for phase in session.phases:
            phase.status = "completed"
            phase.output = f"Output for {phase.name}"

        path = self.rt.archive()
        self.assertTrue(os.path.exists(path))
        with open(path, "r") as f:
            content = f.read()
        self.assertIn("Archive me", content)
        self.assertIn("REQUIREMENTS", content)

    # --- get_state ---

    def test_get_state_no_session(self):
        state = self.rt.get_state()
        self.assertFalse(state["active"])
        self.assertEqual(state["phase_count"], len(DEFAULT_LIFECYCLE_PHASES))
        self.assertEqual(state["skip_phases"], [])

    def test_get_state_with_session(self):
        self.rt.start_session("State check")
        state = self.rt.get_state()
        self.assertTrue(state["active"])
        self.assertIn("session", state)
        self.assertIn("progress", state)

    # --- render_summary ---

    def test_render_summary_active(self):
        self.rt.start_session("Summary check")
        summary = self.rt.render_summary()
        self.assertIn("Lifecycle", summary)
        self.assertIn(DEFAULT_LIFECYCLE_PHASES[0], summary)

    def test_render_summary_no_session(self):
        self.assertEqual(self.rt.render_summary(), "")

    # --- get_prompt_guidance ---

    def test_get_prompt_guidance_active(self):
        self.rt.start_session("Guidance check")
        guidance = self.rt.get_prompt_guidance()
        first_phase = DEFAULT_LIFECYCLE_PHASES[0]
        self.assertIn(first_phase, guidance)
        self.assertIn("Guidance check", guidance)

    def test_get_prompt_guidance_no_session(self):
        self.assertEqual(self.rt.get_prompt_guidance(), "")

    def test_get_prompt_guidance_done(self):
        self.rt.start_session("Done check")
        session = self.rt.get_session()
        for p in session.phases:
            p.status = "completed"
            p.output = f"Output {p.name}"
        session.completed = True
        session.current_phase_index = len(session.phases)

        guidance = self.rt.get_prompt_guidance()
        self.assertIn("DONE", guidance)


class TestLifecycleConstants(unittest.TestCase):
    """Verify lifecycle constants are correct."""

    def test_default_phases_count(self):
        self.assertEqual(len(DEFAULT_LIFECYCLE_PHASES), 10)

    def test_devflow_phases_set(self):
        self.assertIn("ARCHITECTURE", DEVFLOW_PHASES)
        self.assertIn("IMPLEMENTATION", DEVFLOW_PHASES)

    def test_write_phases_set(self):
        self.assertIn("IMPLEMENTATION", WRITE_PHASES)
        self.assertIn("UNIT_TEST", WRITE_PHASES)
        self.assertIn("INTEGRATION_TEST", WRITE_PHASES)

    def test_phase_skill_map_covers_lifecycle_phases(self):
        for name in ["REQUIREMENTS", "SYSTEM_DESIGN", "CODE_REVIEW",
                     "UNIT_TEST", "INTEGRATION_TEST", "ACCEPTANCE"]:
            self.assertIn(name, PHASE_SKILL_MAP,
                          f"{name} should have a skill mapped")

    def test_devflow_phases_not_in_skill_map(self):
        for name in DEVFLOW_PHASES:
            self.assertNotIn(name, PHASE_SKILL_MAP,
                             f"{name} is a DevFlow phase, should not have lifecycle skill")


class TestLifecycleConfig(unittest.TestCase):
    """Test .claw-lifecycle.json config discovery."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_config_skip_phases(self):
        import json
        config = {
            "skip_phases": ["SYSTEM_DESIGN", "INTEGRATION_TEST"]
        }
        config_path = os.path.join(self.tempdir, ".claw-lifecycle.json")
        with open(config_path, "w") as f:
            json.dump(config, f)

        rt = LifecycleRuntime(cwd=self.tempdir)
        session = rt.start_session("Test with config")

        # SYSTEM_DESIGN should be skipped
        system_design = session.get_phase_by_name("SYSTEM_DESIGN")
        self.assertIsNotNone(system_design)
        self.assertEqual(system_design.status, "skipped")

        # INTEGRATION_TEST should be skipped
        integration_test = session.get_phase_by_name("INTEGRATION_TEST")
        self.assertIsNotNone(integration_test)
        self.assertEqual(integration_test.status, "skipped")

        # Other phases should not be skipped
        requirements = session.get_phase_by_name("REQUIREMENTS")
        self.assertEqual(requirements.status, "pending")

    def test_config_custom_phases(self):
        import json
        config = {
            "phases": ["REQUIREMENTS", "IMPLEMENTATION", "ACCEPTANCE"]
        }
        config_path = os.path.join(self.tempdir, ".claw-lifecycle.json")
        with open(config_path, "w") as f:
            json.dump(config, f)

        rt = LifecycleRuntime(cwd=self.tempdir)
        session = rt.start_session("Test custom phases")
        self.assertEqual(len(session.phases), 3)
        names = [p.name for p in session.phases]
        self.assertEqual(names, ["REQUIREMENTS", "IMPLEMENTATION", "ACCEPTANCE"])

    def test_no_config_file(self):
        rt = LifecycleRuntime(cwd=self.tempdir)
        self.assertEqual(rt.get_phase_list(), DEFAULT_LIFECYCLE_PHASES)

    def test_invalid_config_json(self):
        config_path = os.path.join(self.tempdir, ".claw-lifecycle.json")
        with open(config_path, "w") as f:
            f.write("not json {{{")

        rt = LifecycleRuntime(cwd=self.tempdir)
        # Should not crash, fall back to defaults
        self.assertEqual(rt.get_phase_list(), DEFAULT_LIFECYCLE_PHASES)


if __name__ == "__main__":
    unittest.main()
