"""P1 contracts for episode lifecycle, reset, recovery, and replay."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from claw.agent_session import AgentSession
from claw.episode import (
    CheckpointIntegrityError,
    EpisodeManifest,
    EpisodeOrchestrator,
    EpisodeRecoveryManager,
    EpisodeState,
    EpisodeStateError,
    InitialValidationError,
    RerunMode,
    workspace_hash,
)
from claw.experiment.schemas import TaskSpec
from claw.trajectory import (
    ReplayComparator,
    TraceReplayEngine,
    Trajectory,
    rollout_result_to_trajectory,
)


class FakeProcess:
    def __init__(self):
        self.pid = 43210
        self.running = True
        self.terminated = False

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.terminated = True
        self.running = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.running = False


class EpisodeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.template = self.root / "template"
        self.template.mkdir()
        (self.template / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
        self.episodes = self.root / "episodes"

    def tearDown(self):
        self.temp.cleanup()

    def make_task(self, *, initial_passes=False):
        initial_check = (
            'python -c "import sys; sys.exit(0)"'
            if initial_passes
            else 'python -c "import sys; sys.exit(1)"'
        )
        task = TaskSpec(
            task_id="task-1",
            task_version="1.0.0",
            family_id="family-1",
            domain="python-cli",
            task_type="fix_bug",
            difficulty="easy",
            split="train",
            prompt="Set VALUE to 1.",
            template_ref=str(self.template),
            template_hash=workspace_hash(self.template),
            initial_checks=[initial_check],
            test_commands=['python -c "import app; assert app.VALUE == 1"'],
            timeout_seconds=30,
            source="test",
            license="MIT",
        )
        task.content_hash = task.compute_content_hash()
        return task

    def prepare(self, episode_id="ep_test"):
        orchestrator = EpisodeOrchestrator(
            self.episodes,
            project_root=self.root,
            environment_allowlist=["PATH"],
        )
        manifest = orchestrator.prepare(
            self.make_task(),
            episode_id=episode_id,
            runtime_config_ref="runtime-config.v1",
            model_config_ref="model-config.v1",
            prompt_version="prompt.v1",
            tool_version="tools.v1",
        )
        return orchestrator, manifest


class TestEpisodeStateMachine(EpisodeTestCase):
    def test_prepare_preserves_safe_directory_symlink_without_following_loop(self):
        link = self.template / "loop"
        try:
            os.symlink(".", link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        task = self.make_task()
        task.template_hash = workspace_hash(self.template)
        task.content_hash = task.compute_content_hash()
        orchestrator = EpisodeOrchestrator(
            self.episodes,
            project_root=self.root,
            environment_allowlist=["PATH"],
        )
        manifest = orchestrator.prepare(
            task,
            episode_id="ep_symlink",
            runtime_config_ref="runtime-config.v1",
            model_config_ref="model-config.v1",
            prompt_version="prompt.v1",
            tool_version="tools.v1",
        )
        copied = orchestrator.workspace / "loop"
        self.assertEqual(manifest.current_state, EpisodeState.READY)
        self.assertTrue(copied.is_symlink())
        self.assertEqual(os.readlink(copied), ".")

    def test_prepare_rejects_symlink_that_escapes_template(self):
        external = self.root / "external.txt"
        external.write_text("secret\n", encoding="utf-8")
        link = self.template / "external-link"
        try:
            os.symlink("../external.txt", link)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        task = self.make_task()
        task.template_hash = workspace_hash(self.template)
        task.content_hash = task.compute_content_hash()
        orchestrator = EpisodeOrchestrator(
            self.episodes,
            project_root=self.root,
            environment_allowlist=["PATH"],
        )
        with self.assertRaisesRegex(InitialValidationError, "escapes root"):
            orchestrator.prepare(
                task,
                episode_id="ep_escape",
                runtime_config_ref="runtime-config.v1",
                model_config_ref="model-config.v1",
                prompt_version="prompt.v1",
                tool_version="tools.v1",
            )

    def test_prepare_creates_ready_reproducible_episode(self):
        orchestrator, manifest = self.prepare()
        self.assertEqual(manifest.current_state, EpisodeState.READY)
        self.assertTrue(manifest.initial_commit)
        self.assertTrue(manifest.initial_checkpoint_id)
        self.assertEqual(
            workspace_hash(orchestrator.workspace),
            workspace_hash(self.template),
        )
        loaded = EpisodeManifest.load(orchestrator.episode_dir / "episode.json")
        self.assertEqual(loaded.to_dict(), manifest.to_dict())

    def test_transition_is_idempotent_and_rejects_illegal_jump(self):
        _, manifest = self.prepare()
        self.assertFalse(manifest.transition(EpisodeState.READY))
        with self.assertRaises(EpisodeStateError):
            manifest.transition(EpisodeState.ARCHIVED)

    def test_initially_passing_template_is_rejected_with_evidence(self):
        orchestrator = EpisodeOrchestrator(self.episodes, project_root=self.root)
        with self.assertRaises(InitialValidationError):
            orchestrator.prepare(
                self.make_task(initial_passes=True), episode_id="ep_invalid"
            )
        manifest = EpisodeManifest.load(
            self.episodes / "ep_invalid" / "episode.json"
        )
        self.assertEqual(manifest.current_state, EpisodeState.FAILED)
        self.assertIn("unexpectedly passed", manifest.last_error)
        self.assertEqual(
            manifest.metadata["initial_check_results"][0]["returncode"], 0
        )

    def test_initial_check_timeout_is_infrastructure_failure(self):
        task = self.make_task()
        task.initial_checks = [
            'python -c "import time; time.sleep(1)"'
        ]
        task.timeout_seconds = 0.05
        task.content_hash = task.compute_content_hash()
        orchestrator = EpisodeOrchestrator(self.episodes, project_root=self.root)
        with self.assertRaisesRegex(InitialValidationError, "timed out"):
            orchestrator.prepare(task, episode_id="ep_timeout")
        manifest = EpisodeManifest.load(
            self.episodes / "ep_timeout" / "episode.json"
        )
        self.assertEqual(manifest.current_state, EpisodeState.FAILED)
        self.assertTrue(
            manifest.metadata["initial_check_results"][0]["timed_out"]
        )

    def test_archive_is_idempotent_but_cannot_change_conclusion(self):
        orchestrator, _ = self.prepare()
        orchestrator.start_run()
        orchestrator.begin_verification()
        first = orchestrator.archive(
            trajectory_ref="trajectory-1",
            verification_ref="verification-1",
        )
        second = orchestrator.archive(
            trajectory_ref="trajectory-1",
            verification_ref="verification-1",
        )
        self.assertEqual(first.to_dict(), second.to_dict())
        with self.assertRaises(EpisodeStateError):
            orchestrator.archive(
                trajectory_ref="trajectory-2",
                verification_ref="verification-1",
            )


    def test_destroy_cancels_active_episode_and_removes_only_episode_dir(self):
        orchestrator, _ = self.prepare()
        process = FakeProcess()
        orchestrator.register_process(process)
        orchestrator.start_run()
        episode_dir = orchestrator.episode_dir
        orchestrator.destroy()
        self.assertTrue(process.terminated)
        self.assertFalse(episode_dir.exists())
        self.assertTrue(self.episodes.exists())

class TestCheckpointResetAndRerun(EpisodeTestCase):
    def test_checkpoint_reset_restores_files_session_runtime_and_processes(self):
        orchestrator, manifest = self.prepare()
        session = AgentSession(session_id="agent-1")
        session.add_user_message("before checkpoint")
        runtime_state = {"phase": "IMPLEMENTATION", "step": 2}

        (orchestrator.workspace / "app.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        session_cache = orchestrator.workspace / ".port_sessions" / "agent"
        session_cache.mkdir(parents=True)
        (session_cache / "stale.json").write_text("{}", encoding="utf-8")
        process = FakeProcess()
        orchestrator.register_process(process)
        checkpoint = orchestrator.create_checkpoint(
            label="implementation",
            agent_session=session,
            runtime_states={"devflow": runtime_state},
        )
        self.assertIn(process.pid, checkpoint.unfinished_process_ids)

        (orchestrator.workspace / "app.py").write_text(
            "VALUE = 999\n", encoding="utf-8"
        )
        (orchestrator.workspace / "pollution.tmp").write_text(
            "pollution", encoding="utf-8"
        )
        session.add_user_message("after checkpoint")
        restored_runtime = []
        restored_environment = []

        restored_session, states = orchestrator.reset(
            checkpoint_id=checkpoint.checkpoint_id,
            agent_session=session,
            runtime_restorers={"devflow": restored_runtime.append},
            environment_restorer=restored_environment.append,
        )
        self.assertTrue(process.terminated)
        self.assertEqual(
            (orchestrator.workspace / "app.py").read_text(encoding="utf-8"),
            "VALUE = 1\n",
        )
        self.assertFalse((orchestrator.workspace / "pollution.tmp").exists())
        self.assertFalse((orchestrator.workspace / ".port_sessions").exists())
        self.assertIs(restored_session, session)
        self.assertEqual(len(session.messages), 1)
        self.assertEqual(states["devflow"], runtime_state)
        self.assertEqual(restored_runtime, [runtime_state])
        self.assertEqual(restored_environment, [{"PATH": os.environ["PATH"]}])
        self.assertEqual(orchestrator.manifest.current_state, EpisodeState.READY)
        self.assertEqual(orchestrator.manifest.replay_generation, 1)
        first_reset_hash = workspace_hash(orchestrator.workspace)
        (orchestrator.workspace / "app.py").write_text(
            "VALUE = -1\n", encoding="utf-8"
        )
        orchestrator.reset(checkpoint_id=checkpoint.checkpoint_id)
        self.assertEqual(workspace_hash(orchestrator.workspace), first_reset_hash)
        self.assertEqual(orchestrator.manifest.replay_generation, 2)
        self.assertEqual(manifest.episode_id, orchestrator.manifest.episode_id)

    def test_full_rerun_starts_clean_and_records_lineage(self):
        orchestrator, _ = self.prepare()
        (orchestrator.workspace / "pollution.tmp").write_text(
            "pollution", encoding="utf-8"
        )
        orchestrator.start_run()
        orchestrator.start_rerun(
            mode=RerunMode.FULL,
            source_trajectory_ref="trajectory-source",
        )
        self.assertFalse((orchestrator.workspace / "pollution.tmp").exists())
        self.assertEqual(orchestrator.manifest.current_state, EpisodeState.RUNNING)
        self.assertEqual(orchestrator.manifest.rerun_mode, "full-rerun")
        self.assertEqual(
            orchestrator.manifest.source_trajectory_ref, "trajectory-source"
        )
        self.assertEqual(orchestrator.manifest.replay_generation, 1)

    def test_checkpoint_rerun_restores_named_checkpoint_and_lineage(self):
        orchestrator, _ = self.prepare()
        (orchestrator.workspace / "app.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        checkpoint = orchestrator.create_checkpoint(label="step-1")
        (orchestrator.workspace / "app.py").write_text(
            "VALUE = 2\n", encoding="utf-8"
        )

        orchestrator.start_rerun(
            mode=RerunMode.CHECKPOINT,
            source_trajectory_ref="trajectory-source",
            checkpoint_id=checkpoint.checkpoint_id,
        )
        self.assertEqual(
            (orchestrator.workspace / "app.py").read_text(encoding="utf-8"),
            "VALUE = 1\n",
        )
        self.assertEqual(orchestrator.manifest.rerun_mode, "checkpoint-rerun")
        self.assertEqual(
            orchestrator.manifest.source_checkpoint_id, checkpoint.checkpoint_id
        )

    def test_checkpoint_hash_tampering_fails_closed(self):
        orchestrator, _ = self.prepare()
        checkpoint = orchestrator.create_checkpoint(label="tamper-target")
        path = orchestrator.checkpoints.path_for(checkpoint.checkpoint_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["workspace_hash"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaises(CheckpointIntegrityError):
            orchestrator.reset(checkpoint_id=checkpoint.checkpoint_id)
        self.assertEqual(orchestrator.manifest.current_state, EpisodeState.FAILED)


class TestEpisodeRecovery(EpisodeTestCase):
    def test_scan_marks_interrupted_episode_without_touching_workspace(self):
        orchestrator, _ = self.prepare()
        orchestrator.start_run()
        before = workspace_hash(orchestrator.workspace)

        recovered = EpisodeRecoveryManager(self.episodes).scan_and_mark()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(
            recovered[0].current_state, EpisodeState.RECOVERY_REQUIRED
        )
        self.assertEqual(recovered[0].recovery_state, "interrupted_from:RUNNING")
        self.assertEqual(workspace_hash(orchestrator.workspace), before)

        reopened = EpisodeOrchestrator(self.episodes, project_root=self.root)
        reopened.open("ep_test")
        reopened.reset()
        self.assertEqual(reopened.manifest.current_state, EpisodeState.READY)
        self.assertEqual(workspace_hash(reopened.workspace), before)


class TestTraceReplayAndDiff(unittest.TestCase):
    def legacy(self, final_text="done"):
        return {
            "task_id": "task-1",
            "session_id": "session-1",
            "stop_reason": "completed",
            "messages": [
                {"role": "user", "content": "fix it"},
                {"role": "assistant", "content": final_text},
            ],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }

    def test_trace_replay_is_stable_and_does_not_mutate_trajectory(self):
        trajectory = rollout_result_to_trajectory(self.legacy())
        before = trajectory.to_dict()
        engine = TraceReplayEngine()
        first = engine.replay(trajectory)
        second = engine.replay(trajectory)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.event_count, len(trajectory.events))
        self.assertEqual(trajectory.to_dict(), before)

    def test_replay_diff_attributes_model_divergence(self):
        source = rollout_result_to_trajectory(self.legacy("first"))
        rerun_data = source.to_dict()
        response = next(
            item
            for item in rerun_data["events"]
            if item["event_type"] == "model_response"
        )
        response["payload"]["message"]["content"] = "different"
        rerun_data["header"]["trajectory_id"] = "trajectory-rerun"
        rerun = Trajectory.from_dict(rerun_data)

        diff = ReplayComparator().compare(source, rerun)
        self.assertFalse(diff.equivalent)
        self.assertEqual(diff.summary, {"model": 1})

    def test_identical_recorded_facts_are_equivalent(self):
        source = rollout_result_to_trajectory(self.legacy())
        rerun = Trajectory.from_dict(source.to_dict())
        rerun.header.trajectory_id = "trajectory-rerun"
        diff = ReplayComparator().compare(source, rerun)
        self.assertTrue(diff.equivalent)
        self.assertEqual(diff.differences, [])


if __name__ == "__main__":
    unittest.main()
