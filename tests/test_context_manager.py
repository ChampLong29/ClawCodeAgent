"""Tests for ContextManager and phase-boundary session management."""

import unittest

from claw.agent_session import AgentSession
from claw.context_manager import (
    ContextManager,
    PhaseContextPolicy,
    _truncate_to_tokens,
    _get_phase_name_from_boundary,
)


class TestPhaseBoundaryMarkers(unittest.TestCase):
    """AgentSession phase-boundary marking and retrieval."""

    def test_mark_single_boundary(self):
        session = AgentSession(session_id="test-1")
        session.mark_phase_boundary("REQUIREMENTS")
        self.assertEqual(session.phase_boundaries, {"REQUIREMENTS": 0})
        self.assertEqual(len(session.messages), 1)
        msg = session.messages[0]
        self.assertEqual(msg["role"], "system")
        self.assertIn("PHASE_BOUNDARY:REQUIREMENTS", msg["content"])

    def test_mark_multiple_boundaries(self):
        session = AgentSession(session_id="test-2")
        session.add_user_message("build a todo app")
        session.add_assistant_message("I'll help you build a todo app.")
        session.mark_phase_boundary("REQUIREMENTS")
        session.add_assistant_message("Requirements doc...")
        session.mark_phase_boundary("SYSTEM_DESIGN")
        session.add_assistant_message("Design doc...")

        self.assertEqual(
            session.phase_boundaries,
            {"REQUIREMENTS": 2, "SYSTEM_DESIGN": 4},
        )

    def test_get_phase_messages(self):
        session = AgentSession(session_id="test-3")
        session.mark_phase_boundary("PHASE_A")
        session.add_user_message("hello")
        session.add_assistant_message("hi")
        session.mark_phase_boundary("PHASE_B")
        session.add_assistant_message("phase B work")

        phase_a = session.get_phase_messages("PHASE_A")
        self.assertEqual(len(phase_a), 3)  # boundary + user + assistant
        phase_b = session.get_phase_messages("PHASE_B")
        self.assertEqual(len(phase_b), 2)  # boundary + assistant

    def test_get_phase_messages_unknown_phase(self):
        session = AgentSession(session_id="test-4")
        result = session.get_phase_messages("NONEXISTENT")
        self.assertEqual(result, [])

    def test_serialization_roundtrip(self):
        session = AgentSession(session_id="test-5")
        session.mark_phase_boundary("REQUIREMENTS")
        session.add_user_message("q")
        session.add_assistant_message("a")

        data = session.to_dict()
        restored = AgentSession.from_dict(data)

        self.assertEqual(restored.phase_boundaries, session.phase_boundaries)
        self.assertEqual(len(restored.messages), len(session.messages))
        msgs = restored.get_phase_messages("REQUIREMENTS")
        self.assertEqual(len(msgs), 3)


class TestContextManagerCompaction(unittest.TestCase):
    """ContextManager compaction at phase transitions."""

    def setUp(self):
        self.cm = ContextManager()

    def _build_session_with_phases(self):
        """Build a session with two phases."""
        session = AgentSession(session_id="test")
        session.add_system_message("you are a helpful assistant")
        session.add_user_message("build a todo app")
        session.mark_phase_boundary("REQUIREMENTS")
        session.add_assistant_message("Let me gather requirements.")
        session.add_tool_message("call_1", "[file listing...]")
        session.add_tool_message("call_2", "[grep results...]")
        session.add_assistant_message("Here is the requirements document:\n\n"
                                      "## Requirements\n1. Users can create tasks\n"
                                      "2. Users can mark tasks done\n"
                                      "3. Tasks persist to database")
        session.mark_phase_boundary("SYSTEM_DESIGN")
        session.add_assistant_message("Let me design the system.")
        session.add_tool_message("call_3", "[architecture diagram...]")
        session.add_assistant_message(
            "## System Design\n"
            "Backend: FastAPI\n"
            "Database: PostgreSQL\n"
            "Cache: Redis"
        )
        return session

    def test_no_boundaries_no_op(self):
        session = AgentSession(session_id="test")
        session.add_system_message("sys")
        session.add_user_message("hi")
        before = len(session.messages)
        ctx = self.cm.build_context(session, "REQUIREMENTS", {})
        # Session is untouched
        self.assertEqual(len(session.messages), before)
        # Context is same as full messages (no compaction yet)
        self.assertEqual(len(ctx), before)

    def test_compaction_keeps_boundaries(self):
        session = self._build_session_with_phases()
        ctx = self.cm.build_context(
            session,
            current_phase="SYSTEM_DESIGN",
            completed_phase_outputs={
                "REQUIREMENTS": "Requirements: task CRUD, persistence",
            },
        )

        # Context (not session) should have boundaries
        boundary_phases = [_get_phase_name_from_boundary(m) for m in ctx]
        self.assertIn("REQUIREMENTS", boundary_phases)
        self.assertIn("SYSTEM_DESIGN", boundary_phases)
        # Session should be untouched (still has all original messages)
        self.assertGreater(len(session.messages), len(ctx))

    def test_completed_phase_becomes_summary(self):
        session = self._build_session_with_phases()
        req_output = "Requirements: task CRUD, persistence"
        ctx = self.cm.build_context(
            session,
            current_phase="SYSTEM_DESIGN",
            completed_phase_outputs={"REQUIREMENTS": req_output},
        )

        # Context should have summary, session should still have full messages
        summaries = [
            m for m in ctx
            if m.get("metadata", {}).get("phase_summary")
        ]
        self.assertEqual(len(summaries), 1)
        self.assertIn(req_output, summaries[0]["content"])
        # Session still has the original tool messages
        tool_msgs = [m for m in session.messages if m.get("role") == "tool"]
        self.assertGreater(len(tool_msgs), 0)

    def test_completed_phase_tool_results_discarded(self):
        session = self._build_session_with_phases()
        ctx = self.cm.build_context(
            session,
            current_phase="SYSTEM_DESIGN",
            completed_phase_outputs={"REQUIREMENTS": "summary"},
        )
        # Context should NOT have tool messages from completed phases
        summaries = [
            m for m in ctx
            if m.get("metadata", {}).get("phase_summary")
            and m.get("metadata", {}).get("phase_name") == "REQUIREMENTS"
        ]
        self.assertEqual(len(summaries), 1)
        self.assertIn("summary", summaries[0]["content"])

    def test_current_phase_recent_exchanges_preserved(self):
        session = self._build_session_with_phases()
        ctx = self.cm.build_context(
            session,
            current_phase="SYSTEM_DESIGN",
            completed_phase_outputs={"REQUIREMENTS": "summary"},
        )
        # Context should have SD phase assistant messages (last N)
        assistant_msgs = [
            m for m in ctx
            if m.get("role") == "assistant" and "System Design" in m.get("content", "")
        ]
        self.assertGreaterEqual(len(assistant_msgs), 1)

    def test_first_phase_as_current(self):
        """When the first phase is current, it should keep recent exchanges."""
        session = AgentSession(session_id="test")
        session.add_system_message("you are helpful")
        session.add_user_message("build app")
        session.mark_phase_boundary("REQUIREMENTS")
        session.add_assistant_message("Working on requirements...")
        session.add_assistant_message("Requirements: users can log in.")

        ctx = self.cm.build_context(
            session,
            current_phase="REQUIREMENTS",
            completed_phase_outputs={},
        )

        # Context should have preserved messages, session untouched
        self.assertGreaterEqual(len(ctx), 3)
        contents = " ".join(m.get("content", "") for m in ctx)
        self.assertIn("users can log in", contents)


class TestContextManagerExtraction(unittest.TestCase):
    """Structured output extraction."""

    def test_extract_last_assistant_message(self):
        cm = ContextManager()
        session = AgentSession(session_id="test")
        session.mark_phase_boundary("REQUIREMENTS")
        session.add_assistant_message("exploring...")
        session.add_tool_message("t1", "result")
        session.add_assistant_message("final output: requirements doc")

        output = cm.extract_structured_output(session, "REQUIREMENTS")
        self.assertEqual(output, "final output: requirements doc")

    def test_extract_empty_phase(self):
        cm = ContextManager()
        session = AgentSession(session_id="test")
        session.mark_phase_boundary("EMPTY")

        output = cm.extract_structured_output(session, "EMPTY")
        self.assertIsNone(output)


class TestContextInjection(unittest.TestCase):
    """build_phase_context_injection."""

    def test_build_injection_string(self):
        cm = ContextManager()
        result = cm.build_phase_context_injection(
            completed_phase_outputs={
                "REQUIREMENTS": "Users can create and manage tasks",
                "SYSTEM_DESIGN": "FastAPI backend with PostgreSQL",
            },
            current_phase="ARCHITECTURE",
            overall_goal="Build a task manager",
        )
        self.assertIn("REQUIREMENTS", result)
        self.assertIn("SYSTEM_DESIGN", result)
        self.assertIn("ARCHITECTURE", result)

    def test_build_empty(self):
        cm = ContextManager()
        result = cm.build_phase_context_injection({}, "REQUIREMENTS", "goal")
        self.assertEqual(result, "")


class TestHelpers(unittest.TestCase):
    """Utility functions."""

    def test_truncate_short_text(self):
        text = "hello"
        result = _truncate_to_tokens(text, 100)
        self.assertEqual(result, "hello")

    def test_truncate_long_text(self):
        text = "x" * 5000  # 5000 chars ≈ 1250 tokens
        result = _truncate_to_tokens(text, 10)  # 10 tokens ≈ 40 chars
        self.assertLess(len(result), 200)  # roughly
        self.assertIn("truncated", result)

    def test_get_phase_name_from_boundary(self):
        msg = {
            "role": "system",
            "content": "[PHASE_BOUNDARY:ARCHITECTURE]",
            "metadata": {"phase_boundary": True, "phase_name": "ARCHITECTURE"},
        }
        self.assertEqual(_get_phase_name_from_boundary(msg), "ARCHITECTURE")

    def test_get_phase_name_from_non_boundary(self):
        msg = {"role": "user", "content": "hello"}
        self.assertIsNone(_get_phase_name_from_boundary(msg))


if __name__ == "__main__":
    unittest.main()
