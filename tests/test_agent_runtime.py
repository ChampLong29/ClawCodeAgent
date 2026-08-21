"""Tests for agent runtime — session lifecycle, from_session, turn counter."""

import copy
import json
import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from claw.agent_runtime import LocalCodingAgent
from claw.agent_session import AgentSession
from claw.agent_types import ModelConfig, BudgetConfig
from claw.openai_compat import OpenAICompatError
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

    def test_decoding_config_is_sent_to_model_client(self):
        config = ModelConfig(
            name="benchmark-model",
            temperature=0.25,
            max_tokens=321,
        )
        agent = LocalCodingAgent(cwd=self.tempdir, model_config=config)

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.kwargs = None

            def complete(self, **kwargs):
                self.kwargs = kwargs
                return {
                    "role": "assistant",
                    "content": "done",
                    "finish_reason": "stop",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client

        result = agent.run(prompt="test decoding", max_turns=1)

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(client.kwargs["temperature"], 0.25)
        self.assertEqual(client.kwargs["max_tokens"], 321)

    def test_token_limited_empty_response_is_not_completed(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model", max_tokens=4096),
        )

        class TokenLimitedClient:
            model = "benchmark-model"

            def complete(self, **kwargs):
                return {
                    "role": "assistant",
                    "content": "",
                    "finish_reason": "max_tokens",
                    "usage": {"output_tokens": 4096},
                }

        agent.client = TokenLimitedClient()
        result = agent.run(prompt="fix it", max_turns=3)

        self.assertEqual(result.stop_reason, "stopped")
        self.assertIn("token limit", result.error)
        self.assertEqual(agent.session.stop_reason, "stopped")

    def test_completion_reminder_is_injected_once_near_tool_turn_limit(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True, "allow_shell": True},
            completion_reminder_turns=2,
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) == 1:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "list_dir",
                                    "arguments": '{"path": "."}',
                                },
                            }
                        ],
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "done",
                    "finish_reason": "stop",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=3)
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(len(client.requests), 2)
        first_messages = client.requests[0]["messages"]
        second_messages = client.requests[1]["messages"]
        self.assertFalse(
            any("Runtime budget notice" in str(item.get("content")) for item in first_messages)
        )
        reminders = [
            item
            for item in second_messages
            if "Runtime budget notice" in str(item.get("content"))
        ]
        self.assertEqual(len(reminders), 1)
        self.assertIn("2 additional tool-bearing turns", reminders[0]["content"])

    def test_completion_reminder_threshold_rejects_negative_values(self):
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                completion_reminder_turns=-1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                completion_critical_turns=-1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_deadline_turns=-1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_escalation_turns=-1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_escalation_turns=1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_target_read_allowance=-1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_target_read_allowance=2,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_constraint_repair_attempts=-1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_constraint_repair_attempts=2,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_deadline_turns=1,
                implementation_escalation_turns=1,
                force_direct_mutation_after_escalation=True,
                implementation_constraint_repair_attempts=1,
                implementation_path_patterns=("fixed.py",),
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_target_read_allowance=1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                implementation_deadline_turns=1,
                implementation_escalation_turns=1,
                force_direct_mutation_after_escalation=True,
                implementation_target_read_allowance=1,
            )
        with self.assertRaises(ValueError):
            LocalCodingAgent(
                cwd=self.tempdir,
                completion_reminder_turns=2,
                completion_critical_turns=3,
            )

    def test_implementation_deadline_stops_repeating_broad_search(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) == 1:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "inspect-1",
                            "function": {
                                "name": "list_dir",
                                "arguments": '{"path": "."}',
                            },
                        }],
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "done",
                    "finish_reason": "stop",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client

        result = agent.run(prompt="fix it", max_turns=3)

        self.assertEqual(result.stop_reason, "completed")
        notices = [
            item
            for item in client.requests[1]["messages"]
            if "Runtime implementation notice" in str(item.get("content"))
        ]
        self.assertEqual(len(notices), 1)
        self.assertIn("1 tool-bearing turns", notices[0]["content"])

    def test_implementation_escalation_follows_ignored_deadline_once(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
            implementation_escalation_turns=2,
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) <= 4:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": f"inspect-{len(self.requests)}",
                            "function": {
                                "name": "list_dir",
                                "arguments": '{"path": "."}',
                            },
                        }],
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "done",
                    "finish_reason": "stop",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client

        result = agent.run(prompt="fix it", max_turns=6)

        self.assertEqual(result.stop_reason, "completed")
        escalations = [
            item
            for item in client.requests[-1]["messages"]
            if "Runtime implementation escalation" in str(item.get("content"))
        ]
        self.assertEqual(len(escalations), 1)
        self.assertFalse(
            any(
                "Runtime implementation escalation" in str(item.get("content"))
                for item in client.requests[2]["messages"]
            )
        )
        self.assertTrue(
            any(
                "Runtime implementation escalation" in str(item.get("content"))
                for item in client.requests[3]["messages"]
            )
        )
        self.assertEqual(len(client.requests), 5)

    def test_escalation_can_force_one_bounded_direct_mutation_request(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(
                name="benchmark-model",
                max_tokens=2048,
                thinking_mode="disabled",
            ),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
            implementation_escalation_turns=1,
            force_direct_mutation_after_escalation=True,
            implementation_path_patterns=("fixed.py",),
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) <= 2:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": f"inspect-{len(self.requests)}",
                            "function": {
                                "name": "list_dir",
                                "arguments": '{"path": "."}',
                            },
                        }],
                        "usage": {},
                    }
                if len(self.requests) == 3:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "forced-edit",
                            "function": {
                                "name": "write_file",
                                "arguments": (
                                    '{"path": "fixed.py", '
                                    '"content": "VALUE = 1\\n"}'
                                ),
                            },
                        }],
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "done",
                    "finish_reason": "stop",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=5)

        self.assertEqual(result.stop_reason, "completed")
        forced = client.requests[2]
        self.assertEqual(forced["tool_choice"], "required")
        self.assertEqual(forced["thinking_mode"], "disabled")
        self.assertEqual(forced["max_tokens"], 2048)
        visible_names = {
            item.get("name")
            or (item.get("function") or {}).get("name")
            for item in forced["tools"]
        }
        self.assertEqual(visible_names, {"write_file", "edit_file"})
        self.assertTrue((Path(self.tempdir) / "fixed.py").is_file())

    def test_escalation_can_allow_one_target_read_then_requires_edit(self):
        (Path(self.tempdir) / "fixed.py").write_text("VALUE = 0\n")
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
            implementation_escalation_turns=1,
            force_direct_mutation_after_escalation=True,
            implementation_target_read_allowance=1,
            implementation_path_patterns=("fixed.py",),
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                request_number = len(self.requests)
                if request_number <= 2:
                    name, arguments = "list_dir", '{"path": "."}'
                elif request_number == 3:
                    name, arguments = "read_file", '{"path": "fixed.py"}'
                elif request_number == 4:
                    name = "write_file"
                    arguments = '{"path": "fixed.py", "content": "VALUE = 1\\n"}'
                else:
                    return {"content": "done", "usage": {}}
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": f"call-{request_number}",
                        "function": {"name": name, "arguments": arguments},
                    }],
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=6)

        self.assertEqual(result.stop_reason, "completed")
        target_read_tools = {
            item.get("name") or (item.get("function") or {}).get("name")
            for item in client.requests[2]["tools"]
        }
        self.assertEqual(
            target_read_tools, {"read_file", "write_file", "edit_file"}
        )
        edit_only_tools = {
            item.get("name") or (item.get("function") or {}).get("name")
            for item in client.requests[3]["tools"]
        }
        self.assertEqual(edit_only_tools, {"write_file", "edit_file"})
        self.assertEqual(
            (Path(self.tempdir) / "fixed.py").read_text(), "VALUE = 1\n"
        )

    def test_escalation_rejects_off_target_read_without_dispatch(self):
        (Path(self.tempdir) / "other.py").write_text("VALUE = 0\n")
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
            implementation_escalation_turns=1,
            force_direct_mutation_after_escalation=True,
            implementation_target_read_allowance=1,
            implementation_path_patterns=("fixed.py",),
        )

        class OffTargetClient:
            model = "benchmark-model"

            def __init__(self):
                self.calls = 0

            def complete(self, **kwargs):
                self.calls += 1
                name = "list_dir" if self.calls <= 2 else "read_file"
                path = "." if self.calls <= 2 else "other.py"
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": f"call-{self.calls}",
                        "function": {
                            "name": name,
                            "arguments": json.dumps({"path": path}),
                        },
                    }],
                    "usage": {},
                }

        agent.client = OffTargetClient()
        result = agent.run(prompt="fix it", max_turns=5)

        self.assertEqual(result.stop_reason, "stopped")
        self.assertIn("bounded implementation", result.error)
        self.assertEqual(len(agent.session.messages), 5)

    def test_escalation_rejects_second_target_read(self):
        (Path(self.tempdir) / "fixed.py").write_text("VALUE = 0\n")
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
            implementation_escalation_turns=1,
            force_direct_mutation_after_escalation=True,
            implementation_target_read_allowance=1,
            implementation_path_patterns=("fixed.py",),
        )

        class RepeatingReadClient:
            model = "benchmark-model"

            def __init__(self):
                self.calls = 0

            def complete(self, **kwargs):
                self.calls += 1
                name = "list_dir" if self.calls <= 2 else "read_file"
                path = "." if self.calls <= 2 else "fixed.py"
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": f"call-{self.calls}",
                        "function": {
                            "name": name,
                            "arguments": json.dumps({"path": path}),
                        },
                    }],
                    "usage": {},
                }

        client = RepeatingReadClient()
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=6)

        self.assertEqual(result.stop_reason, "stopped")
        self.assertEqual(client.calls, 4)
        self.assertIn("bounded implementation", result.error)

    def test_constraint_repair_can_recover_repeated_read_into_edit(self):
        (Path(self.tempdir) / "fixed.py").write_text("VALUE = 0\n")
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
            implementation_escalation_turns=1,
            force_direct_mutation_after_escalation=True,
            implementation_target_read_allowance=1,
            implementation_constraint_repair_attempts=1,
            implementation_path_patterns=("fixed.py",),
        )

        class CapturingObserver:
            def __init__(self):
                self.events = []

            def on_run_start(self, *args, **kwargs):
                pass

            def on_run_finish(self, *args, **kwargs):
                pass

            def record(self, event_type, **kwargs):
                self.events.append((event_type, copy.deepcopy(kwargs)))
                return f"event-{len(self.events)}"

        class RepairingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                request_number = len(self.requests)
                if request_number <= 2:
                    name, arguments = "list_dir", '{"path": "."}'
                elif request_number in {3, 4}:
                    name, arguments = "read_file", '{"path": "fixed.py"}'
                elif request_number == 5:
                    name = "write_file"
                    arguments = '{"path": "fixed.py", "content": "VALUE = 1\\n"}'
                else:
                    return {"content": "done", "usage": {}}
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": f"call-{request_number}",
                        "function": {"name": name, "arguments": arguments},
                    }],
                    "usage": {},
                }

        observer = CapturingObserver()
        client = RepairingClient()
        agent.runtime_observer = observer
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=7)

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(len(client.requests), 6)
        self.assertEqual(
            (Path(self.tempdir) / "fixed.py").read_text(), "VALUE = 1\n"
        )
        correction_messages = [
            item
            for item in client.requests[4]["messages"]
            if "Runtime action-constraint correction" in str(item.get("content"))
        ]
        self.assertEqual(len(correction_messages), 1)
        visible_names = {
            item.get("name") or (item.get("function") or {}).get("name")
            for item in client.requests[4]["tools"]
        }
        self.assertEqual(visible_names, {"write_file", "edit_file"})
        tool_calls = [
            kwargs["payload"]["tool_name"]
            for event_type, kwargs in observer.events
            if event_type == "tool_call"
        ]
        self.assertEqual(tool_calls.count("read_file"), 1)
        repairs = [
            kwargs["payload"]
            for event_type, kwargs in observer.events
            if event_type == "runtime_guidance"
            and kwargs["payload"].get("guidance_type")
            == "implementation_action_constraint_repair"
        ]
        self.assertEqual(len(repairs), 1)
        self.assertTrue(repairs[0]["rejected_before_dispatch"])
        self.assertFalse(repairs[0]["new_task_information_provided"])

    def test_constraint_repair_stops_after_one_failed_correction(self):
        (Path(self.tempdir) / "fixed.py").write_text("VALUE = 0\n")
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
            implementation_escalation_turns=1,
            force_direct_mutation_after_escalation=True,
            implementation_target_read_allowance=1,
            implementation_constraint_repair_attempts=1,
            implementation_path_patterns=("fixed.py",),
        )

        class CapturingObserver:
            def __init__(self):
                self.events = []

            def on_run_start(self, *args, **kwargs):
                pass

            def on_run_finish(self, *args, **kwargs):
                pass

            def record(self, event_type, **kwargs):
                self.events.append((event_type, copy.deepcopy(kwargs)))
                return f"event-{len(self.events)}"

        class NonCompliantClient:
            model = "benchmark-model"

            def __init__(self):
                self.calls = 0

            def complete(self, **kwargs):
                self.calls += 1
                name = "list_dir" if self.calls <= 2 else "read_file"
                path = "." if self.calls <= 2 else "fixed.py"
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": f"call-{self.calls}",
                        "function": {
                            "name": name,
                            "arguments": json.dumps({"path": path}),
                        },
                    }],
                    "usage": {},
                }

        observer = CapturingObserver()
        client = NonCompliantClient()
        agent.runtime_observer = observer
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=7)

        self.assertEqual(result.stop_reason, "stopped")
        self.assertEqual(client.calls, 5)
        self.assertEqual(
            (Path(self.tempdir) / "fixed.py").read_text(), "VALUE = 0\n"
        )
        tool_calls = [
            kwargs["payload"]["tool_name"]
            for event_type, kwargs in observer.events
            if event_type == "tool_call"
        ]
        self.assertEqual(tool_calls.count("read_file"), 1)
        stops = [
            kwargs["payload"]
            for event_type, kwargs in observer.events
            if event_type == "runtime_stop"
        ]
        self.assertEqual(stops[-1]["reason"], "action_constraint_unsatisfied")
        self.assertEqual(stops[-1]["constraint_repairs_used"], 1)
        self.assertTrue(stops[-1]["constraint_repairs_exhausted"])

    def test_successful_edit_suppresses_implementation_escalation(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            implementation_deadline_turns=1,
            implementation_escalation_turns=1,
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) == 1:
                    tool_name = "list_dir"
                    arguments = '{"path": "."}'
                elif len(self.requests) == 2:
                    tool_name = "write_file"
                    arguments = '{"path": "fixed.py", "content": "VALUE = 1\\n"}'
                else:
                    return {
                        "role": "assistant",
                        "content": "done",
                        "finish_reason": "stop",
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": f"call-{len(self.requests)}",
                        "function": {
                            "name": tool_name,
                            "arguments": arguments,
                        },
                    }],
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client

        result = agent.run(prompt="fix it", max_turns=5)

        self.assertEqual(result.stop_reason, "completed")
        self.assertTrue((Path(self.tempdir) / "fixed.py").is_file())
        self.assertFalse(
            any(
                "Runtime implementation escalation" in str(item.get("content"))
                for request in client.requests
                for item in request["messages"]
            )
        )

    def test_successful_edit_triggers_one_post_edit_contract_notice(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            post_edit_contract_guidance=True,
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) == 1:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "edit-1",
                            "function": {
                                "name": "write_file",
                                "arguments": (
                                    '{"path": "fixed.py", '
                                    '"content": "VALUE = 1\\n"}'
                                ),
                            },
                        }],
                        "usage": {},
                    }
                if len(self.requests) == 2:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "inspect-1",
                            "function": {
                                "name": "list_dir",
                                "arguments": '{"path": "."}',
                            },
                        }],
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "done",
                    "finish_reason": "stop",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client

        result = agent.run(prompt="fix it", max_turns=4)

        self.assertEqual(result.stop_reason, "completed")
        notices = [
            item
            for item in client.requests[1]["messages"]
            if "Runtime post-edit contract notice" in str(item.get("content"))
        ]
        self.assertEqual(len(notices), 1)
        final_notices = [
            item
            for item in client.requests[2]["messages"]
            if "Runtime post-edit contract notice" in str(item.get("content"))
        ]
        self.assertEqual(len(final_notices), 1)
        self.assertIn("container and return types", notices[0]["content"])
        self.assertIn("non-default configuration", notices[0]["content"])
        self.assertIn("parent chain", notices[0]["content"])
        self.assertIn("which object owns", notices[0]["content"])
        self.assertIn("setting the value directly", notices[0]["content"])

    def test_scratch_edit_does_not_trigger_implementation_contract_notice(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True},
            post_edit_contract_guidance=True,
            implementation_path_patterns=("package/source.py",),
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                path = "repro.py" if len(self.requests) == 1 else "package/source.py"
                if len(self.requests) <= 2:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": f"edit-{len(self.requests)}",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps(
                                    {"path": path, "content": "VALUE = 1\n"}
                                ),
                            },
                        }],
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "done",
                    "finish_reason": "stop",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=4)

        self.assertEqual(result.stop_reason, "completed")
        scratch_request = client.requests[1]["messages"]
        self.assertFalse(
            any(
                "Runtime post-edit contract notice" in str(item.get("content"))
                for item in scratch_request
            )
        )
        implementation_request = client.requests[2]["messages"]
        self.assertTrue(
            any(
                "Runtime post-edit contract notice" in str(item.get("content"))
                for item in implementation_request
            )
        )

    def test_completion_critical_notice_follows_early_warning(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(name="benchmark-model"),
            permissions={"allow_write": True, "allow_shell": True},
            completion_reminder_turns=3,
            completion_critical_turns=1,
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) <= 3:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"call-{len(self.requests)}",
                                "function": {
                                    "name": "list_dir",
                                    "arguments": '{"path": "."}',
                                },
                            }
                        ],
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "done",
                    "finish_reason": "stop",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=4)
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(len(client.requests), 4)
        warning_messages = client.requests[1]["messages"]
        critical_messages = client.requests[3]["messages"]
        self.assertTrue(
            any(
                "Runtime budget notice" in str(item.get("content"))
                for item in warning_messages
            )
        )
        self.assertFalse(
            any(
                "Runtime finalization notice" in str(item.get("content"))
                for item in warning_messages
            )
        )
        self.assertEqual(
            sum(
                "Runtime finalization notice" in str(item.get("content"))
                for item in critical_messages
            ),
            1,
        )

    def test_critical_turn_can_force_final_response_without_tools(self):
        agent = LocalCodingAgent(
            cwd=self.tempdir,
            model_config=ModelConfig(
                name="benchmark-model",
                thinking_mode="disabled",
            ),
            permissions={"allow_write": True},
            completion_reminder_turns=2,
            completion_critical_turns=1,
            force_final_response_at_critical=True,
        )

        class CapturingClient:
            model = "benchmark-model"

            def __init__(self):
                self.requests = []

            def complete(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) < 3:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": f"inspect-{len(self.requests)}",
                            "function": {
                                "name": "list_dir",
                                "arguments": '{"path": "."}',
                            },
                        }],
                        "usage": {},
                    }
                return {
                    "role": "assistant",
                    "content": "implemented and verified",
                    "finish_reason": "end_turn",
                    "usage": {},
                }

        client = CapturingClient()
        agent.client = client
        result = agent.run(prompt="fix it", max_turns=3)

        self.assertEqual(result.stop_reason, "completed")
        constrained = client.requests[2]
        self.assertEqual(constrained["tool_choice"], "none")
        self.assertEqual(constrained["tools"], [])
        self.assertEqual(result.final_message, "implemented and verified")

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


class TestAgentAPIRetry(unittest.TestCase):
    def test_retries_connection_reset_without_replaying_tools(self):
        agent = object.__new__(LocalCodingAgent)
        calls = []

        def request():
            calls.append(True)
            if len(calls) == 1:
                raise ConnectionResetError("remote closed connection")
            return {"role": "assistant", "content": "done"}

        with patch("claw.agent_runtime.time.sleep") as sleep:
            result = agent._retry_call(request)
        self.assertEqual(result["content"], "done")
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once()

    def test_retries_wrapped_connection_error(self):
        agent = object.__new__(LocalCodingAgent)
        calls = []

        def request():
            calls.append(True)
            if len(calls) == 1:
                raise OpenAICompatError("Connection error: reset")
            return "done"

        with patch("claw.agent_runtime.time.sleep"):
            self.assertEqual(agent._retry_call(request), "done")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
