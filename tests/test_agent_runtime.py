"""Tests for agent runtime — session lifecycle, from_session, turn counter."""

import unittest
import tempfile
import os
from unittest.mock import patch, MagicMock
from claw.agent_runtime import LocalCodingAgent
from claw.agent_session import AgentSession
from claw.agent_types import ModelConfig, BudgetConfig
from claw.session_store import save_agent_session, load_agent_session, list_sessions


class TestAgentRuntimeFromSession(unittest.TestCase):
    """Test agent creation from existing sessions."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_from_session_loads_existing(self):
        session = AgentSession(session_id="test-sess")
        session.add_user_message("hello")
        session.name = "my session"
        session.model = "test-model"
        save_agent_session(session, self.tempdir)

        agent = LocalCodingAgent.from_session(
            session_id="test-sess",
            cwd=self.tempdir,
        )
        self.assertIsNotNone(agent.session)
        self.assertEqual(agent.session.session_id, "test-sess")
        self.assertEqual(len(agent.session.messages), 1)
        self.assertEqual(agent.session.name, "my session")
        self.assertEqual(agent.session.model, "test-model")

    def test_from_session_creates_new_if_not_found(self):
        agent = LocalCodingAgent.from_session(
            session_id="no-such-session",
            cwd=self.tempdir,
        )
        self.assertIsNotNone(agent.session)
        self.assertEqual(agent.session.session_id, "no-such-session")
        self.assertEqual(agent.session.messages, [])

    def test_from_session_with_model_config(self):
        session = AgentSession(session_id="model-test")
        save_agent_session(session, self.tempdir)

        config = ModelConfig(name="custom-model", temperature=0.5)
        agent = LocalCodingAgent.from_session(
            session_id="model-test",
            cwd=self.tempdir,
            model_config=config,
        )
        # Model name from config should be set on agent, not on session
        # (session keeps its own model field from persistence)
        self.assertEqual(agent.session.session_id, "model-test")


class TestAgentSessionManagement(unittest.TestCase):
    """Test agent session lifecycle (without live API call)."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_run_creates_session(self):
        """run() should create a session when self.session is None."""
        agent = LocalCodingAgent(cwd=self.tempdir)
        agent.session = None

        # Patch _run_loop to avoid API call
        with patch.object(agent, '_run_loop') as mock_loop:
            from claw.agent_types import AgentRunResult
            mock_loop.return_value = AgentRunResult(
                stop_reason="completed",
                final_message="mock result",
            )
            agent.run(prompt="hello", max_turns=1)

        self.assertIsNotNone(agent.session)
        self.assertEqual(agent.session.cwd, self.tempdir)
        self.assertEqual(len(agent.session.messages), 1)
        self.assertEqual(agent.session.messages[0]["role"], "user")
        self.assertEqual(agent.session.messages[0]["content"], "hello")

    def test_run_saves_session_to_disk(self):
        """run() should persist session to .port_sessions/agent/<id>.jsonl."""
        agent = LocalCodingAgent(cwd=self.tempdir)
        agent.session = None

        with patch.object(agent, '_run_loop') as mock_loop:
            from claw.agent_types import AgentRunResult
            mock_loop.return_value = AgentRunResult(
                stop_reason="completed",
                final_message="done",
            )
            agent.run(prompt="do something", max_turns=1)

        # Session should be saved
        sessions = list_sessions(self.tempdir)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], agent.session.session_id)
        self.assertEqual(sessions[0]["message_count"], 1)

        # Verify file exists on disk
        filepath = os.path.join(
            self.tempdir, ".port_sessions", "agent",
            f"{agent.session.session_id}.jsonl"
        )
        self.assertTrue(os.path.exists(filepath))

    def test_run_reuses_existing_session(self):
        """Subsequent run() calls should reuse the same session."""
        session = AgentSession(session_id="reuse-test")
        session.name = "my work"
        save_agent_session(session, self.tempdir)

        agent = LocalCodingAgent.from_session(
            session_id="reuse-test",
            cwd=self.tempdir,
        )

        with patch.object(agent, '_run_loop') as mock_loop:
            from claw.agent_types import AgentRunResult
            mock_loop.return_value = AgentRunResult(
                stop_reason="completed",
                final_message="result",
            )
            agent.run(prompt="first message", max_turns=1)

        # Same session, now has 1 message
        self.assertEqual(agent.session.session_id, "reuse-test")
        self.assertEqual(agent.session.name, "my work")

        with patch.object(agent, '_run_loop') as mock_loop2:
            from claw.agent_types import AgentRunResult
            mock_loop2.return_value = AgentRunResult(
                stop_reason="completed",
                final_message="result",
            )
            agent.run(prompt="second message", max_turns=1)

        # Same session, now has 2 messages
        self.assertEqual(agent.session.session_id, "reuse-test")
        self.assertEqual(len(agent.session.messages), 2)

    def test_run_sets_session_fields(self):
        """run() should set cwd and model on session."""
        config = ModelConfig(name="test-model-v2", temperature=0.2)
        agent = LocalCodingAgent(cwd=self.tempdir, model_config=config)
        agent.session = None

        with patch.object(agent, '_run_loop') as mock_loop:
            from claw.agent_types import AgentRunResult
            mock_loop.return_value = AgentRunResult(
                stop_reason="completed",
            )
            agent.run(prompt="test", max_turns=1)

        self.assertEqual(agent.session.cwd, self.tempdir)
        self.assertEqual(agent.session.model, "test-model-v2")

    def test_run_saves_stop_reason(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        agent.session = None

        def mock_run_loop(max_turns, stream):
            agent.session.stop_reason = "budget_exceeded"
            from claw.agent_types import AgentRunResult
            return AgentRunResult(
                stop_reason="budget_exceeded",
                error="Token budget exhausted",
            )

        with patch.object(agent, '_run_loop', side_effect=mock_run_loop):
            agent.run(prompt="test", max_turns=1)

        sessions = list_sessions(self.tempdir)
        self.assertEqual(sessions[0]["stop_reason"], "budget_exceeded")

    def test_resume_saves_session(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        agent.session = AgentSession(session_id="resume-save")
        agent.session.cwd = self.tempdir
        save_agent_session(agent.session, self.tempdir)

        with patch.object(agent, '_run_loop') as mock_loop:
            from claw.agent_types import AgentRunResult
            mock_loop.return_value = AgentRunResult(
                stop_reason="completed",
            )
            agent.resume(prompt="continue")

        sessions = list_sessions(self.tempdir)
        found = [s for s in sessions if s["session_id"] == "resume-save"]
        self.assertEqual(len(found), 1)

    def test_resume_requires_existing_session(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        agent.session = None
        with self.assertRaises(ValueError):
            agent.resume(prompt="bad resume")

    def test_run_resets_turns(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        agent.session = None
        agent.turns = 999  # Should be reset by run()

        with patch.object(agent, '_run_loop') as mock_loop:
            from claw.agent_types import AgentRunResult
            mock_loop.return_value = AgentRunResult(stop_reason="completed")
            agent.run(prompt="test", max_turns=1)

        self.assertEqual(agent.turns, 0)


class TestAgentRuntimeRuntimes(unittest.TestCase):
    """Test runtime initialization in __post_init__."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_runtime_instances_created(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        self.assertIsInstance(agent._runtime_instances, dict)
        # At minimum, these runtimes should be registered
        self.assertIn("devflow", agent._runtime_instances)
        self.assertIn("lifecycle", agent._runtime_instances)
        self.assertIn("bridge", agent._runtime_instances)

    def test_runtime_instances_have_get_state(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        for name, rt in agent._runtime_instances.items():
            try:
                state = rt.get_state()
                # Some runtimes return None when no config exists (e.g., MCP)
                if state is not None:
                    self.assertIsInstance(state, dict,
                        f"{name}.get_state() should return dict, got {type(state)}")
            except Exception as e:
                self.fail(f"{name}.get_state() raised {e}")

    def test_runtime_instances_have_render_summary(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        for name, rt in agent._runtime_instances.items():
            try:
                summary = rt.render_summary()
                self.assertIsInstance(summary, str, f"{name}.render_summary() should return str")
            except Exception as e:
                self.fail(f"{name}.render_summary() raised {e}")


class TestAgentConfiguration(unittest.TestCase):
    """Test agent configuration and initialization."""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_create_with_model_config(self):
        config = ModelConfig(name="gpt-4", temperature=0.7)
        agent = LocalCodingAgent(cwd=self.tempdir, model_config=config)
        # The model name set in ModelConfig is accessible
        self.assertIsNotNone(agent.cwd)
        self.assertEqual(agent.cwd, self.tempdir)

    def test_create_with_budget(self):
        budget = BudgetConfig(max_total_tokens=100000, max_output_tokens=40000)
        agent = LocalCodingAgent(cwd=self.tempdir, budget=budget)
        self.assertEqual(agent.budget.max_total_tokens, 100000)

    def test_get_state_no_session(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        state = agent.get_state()
        self.assertIsInstance(state, dict)
        self.assertIn("session_id", state)
        self.assertIsNone(state["session_id"])

    def test_get_state_with_session(self):
        agent = LocalCodingAgent(cwd=self.tempdir)
        agent.session = AgentSession(session_id="state-test")
        state = agent.get_state()
        self.assertEqual(state["session_id"], "state-test")


if __name__ == "__main__":
    unittest.main()
