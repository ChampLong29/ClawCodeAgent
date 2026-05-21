"""End-to-end lifecycle flow tests — verify the full phase execution cycle."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from claw.agent_session import AgentSession
from claw.agent_types import AgentRunResult, UsageStats
from claw.lifecycle_runtime import LifecycleRuntime


class FakeAgent:
    """Minimal fake agent that returns canned responses."""

    def __init__(self, responses=None):
        self.responses = responses or []
        self._call_count = 0
        self.session = AgentSession(session_id="fake-agent-session")
        self.permissions = {"allow_write": False, "allow_shell": False}
        self._blocked_tools = []

    def run(self, prompt="", stream=False, max_turns=None):
        if self._call_count < len(self.responses):
            output = self.responses[self._call_count]
        else:
            output = "Default output from fake agent."
        self._call_count += 1
        return AgentRunResult(
            stop_reason="completed",
            final_message=output,
            usage=UsageStats(),
        )

    def set_blocked_tools(self, tool_names):
        self._blocked_tools = list(tool_names)

    def clear_blocked_tools(self):
        self._blocked_tools = []


class TestLifecycleFullFlow(unittest.TestCase):
    """Simulate a complete lifecycle run with a fake agent."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rt = LifecycleRuntime(cwd=self.tmpdir)
        self.agent = FakeAgent(responses=[
            # REQUIREMENTS phase output
            "## Requirements\n"
            "1. User registration and login\n"
            "2. Club creation and editing\n"
            "3. Member management (add/remove)\n"
            "4. Club activity management",
            # SYSTEM_DESIGN phase output
            "## System Design\n"
            "- Frontend: React + TypeScript\n"
            "- Backend: FastAPI\n"
            "- Database: PostgreSQL\n"
            "- Auth: JWT tokens",
            # ARCHITECTURE phase output (delegates to DevFlow)
            "## Architecture\n"
            "### Overview\nMonorepo with frontend/ and backend/ directories\n"
            "### Components\n- Frontend SPA\n- REST API\n- PostgreSQL database",
        ])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_start_session_first_phase_is_requirements(self):
        """After starting, the first phase should be REQUIREMENTS (pending)."""
        session = self.rt.start_session("Build a club management system")
        phase = session.get_current_phase()
        self.assertEqual(phase.name, "REQUIREMENTS")
        self.assertEqual(phase.status, "pending")

    def test_execute_phase_sets_status_and_output(self):
        """After execute_phase, phase should be 'in_progress' with output."""
        self.rt.start_session("Build a club management system")
        output = self.rt.execute_phase(self.agent)

        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.status, "in_progress")
        self.assertIsNotNone(phase.output)
        self.assertIn("Requirements", output)

    def test_full_phase_cycle(self):
        """Execute → accept → next phase → execute → accept → ..."""
        self.rt.start_session("Build a club management system")

        # ---- REQUIREMENTS ----
        output = self.rt.execute_phase(self.agent)
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.name, "REQUIREMENTS")
        self.assertEqual(phase.status, "in_progress")
        self.assertIn("Requirements", output)

        # Accept → advance
        has_next = self.rt.advance_phase(agent_session=self.agent.session)
        self.assertTrue(has_next)

        # ---- SYSTEM_DESIGN ----
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.name, "SYSTEM_DESIGN")
        self.assertEqual(phase.status, "pending")

        output = self.rt.execute_phase(self.agent)
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.status, "in_progress")
        self.assertIn("System Design", output)

        # Accept → advance
        has_next = self.rt.advance_phase(agent_session=self.agent.session)
        self.assertTrue(has_next)

        # ---- ARCHITECTURE (delegates to DevFlow) ----
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.name, "ARCHITECTURE")

        output = self.rt.execute_phase(self.agent)
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.status, "in_progress")
        self.assertIn("Architecture", output)

    def test_reject_resets_and_reexecute(self):
        """Rejecting a phase should reset it, then re-execute works."""
        self.rt.start_session("Build app")
        self.rt.execute_phase(self.agent)

        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.status, "in_progress")

        # Reject
        self.rt.retry_phase()
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.status, "pending")
        self.assertIsNone(phase.output)

        # Re-execute
        output = self.rt.execute_phase(self.agent)
        self.assertIsNotNone(output)
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.status, "in_progress")

    def test_rollback_and_reexecute(self):
        """After advancing past a phase, roll back and re-execute."""
        self.rt.start_session("Build app")

        # Complete REQUIREMENTS
        self.rt.execute_phase(self.agent)
        self.rt.advance_phase(agent_session=self.agent.session)

        # Complete SYSTEM_DESIGN
        self.rt.execute_phase(self.agent)
        self.rt.advance_phase(agent_session=self.agent.session)

        # Now at ARCHITECTURE
        self.assertEqual(self.rt.session.get_current_phase().name, "ARCHITECTURE")

        # Roll back to REQUIREMENTS
        ok = self.rt.rollback_to_phase("REQUIREMENTS")
        self.assertTrue(ok)
        self.assertEqual(self.rt.session.get_current_phase().name, "REQUIREMENTS")

        # REQUIREMENTS should be pending again
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.status, "pending")

        # Re-execute
        output = self.rt.execute_phase(self.agent)
        self.assertIsNotNone(output)
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.status, "in_progress")

    def test_phase_outputs_not_lost_on_advance(self):
        """After advancing, previous phase output should be stored."""
        self.rt.start_session("Build app")
        self.rt.execute_phase(self.agent)
        self.rt.advance_phase(agent_session=self.agent.session)

        # REQUIREMENTS should be "completed" with output preserved
        req_phase = self.rt.session.phases[0]  # REQUIREMENTS is first
        self.assertEqual(req_phase.status, "completed")
        self.assertIsNotNone(req_phase.output)
        self.assertIn("Requirements", req_phase.output)

    def test_context_manager_compaction(self):
        """Advancing phases should trigger context compaction."""
        self.rt.start_session("Build app")

        # Mark a phase boundary in the agent session
        agent_session = self.agent.session
        agent_session.mark_phase_boundary("REQUIREMENTS")
        agent_session.add_assistant_message("some exploration")
        agent_session.add_tool_message("t1", "tool result")
        agent_session.add_assistant_message("final requirements doc")

        # Execute and advance
        self.rt.execute_phase(self.agent)
        self.rt.advance_phase(agent_session=agent_session)

        # Agent session should have been compacted
        # The requirements phase messages should have been summarized
        self.assertIn("REQUIREMENTS", agent_session.phase_boundaries)

        # Check that system prompt + user message are preserved
        roles = [m["role"] for m in agent_session.messages]
        self.assertIn("system", roles)

    def test_skip_phase(self):
        """Skipping should mark phase as skipped and advance."""
        self.rt.start_session("Build app")
        self.rt.execute_phase(self.agent)

        has_next = self.rt.skip_phase(agent_session=self.agent.session)
        self.assertTrue(has_next)

        # REQUIREMENTS should be skipped
        req_phase = self.rt.session.phases[0]
        self.assertEqual(req_phase.status, "skipped")
        self.assertEqual(req_phase.output, "Skipped by user.")

        # Should be at SYSTEM_DESIGN
        phase = self.rt.session.get_current_phase()
        self.assertEqual(phase.name, "SYSTEM_DESIGN")


if __name__ == "__main__":
    unittest.main()


class TestActionMasking(unittest.TestCase):
    """Test that phase-based action masking actually blocks tools."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rt = LifecycleRuntime(cwd=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_requirements_phase_blocks_write_tools(self):
        """REQUIREMENTS phase must block write_file, edit_file, bash."""
        blocked = self.rt.get_blocked_tools_for_phase("REQUIREMENTS")
        self.assertIn("write_file", blocked)
        self.assertIn("edit_file", blocked)
        self.assertIn("bash", blocked)

    def test_requirements_phase_allows_read_tools(self):
        """REQUIREMENTS phase must allow read-only tools."""
        blocked = self.rt.get_blocked_tools_for_phase("REQUIREMENTS")
        self.assertNotIn("read_file", blocked)
        self.assertNotIn("list_dir", blocked)
        self.assertNotIn("glob_search", blocked)
        self.assertNotIn("grep_search", blocked)

    def test_implementation_phase_allows_all(self):
        """IMPLEMENTATION phase must allow write and shell tools."""
        blocked = self.rt.get_blocked_tools_for_phase("IMPLEMENTATION")
        self.assertNotIn("write_file", blocked)
        self.assertNotIn("edit_file", blocked)
        self.assertNotIn("bash", blocked)
        self.assertNotIn("read_file", blocked)

    def test_code_review_blocks_write_allows_shell(self):
        """CODE_REVIEW phase allows bash (for tests) but blocks writes."""
        blocked = self.rt.get_blocked_tools_for_phase("CODE_REVIEW")
        self.assertIn("write_file", blocked)
        self.assertIn("edit_file", blocked)
        self.assertNotIn("bash", blocked)
        self.assertNotIn("read_file", blocked)

    def test_apply_phase_constraints_sets_permissions(self):
        """apply_phase_constraints should set permissions and blocked tools."""
        agent = FakeAgent()
        self.rt.apply_phase_constraints(agent, "REQUIREMENTS")

        # Write and shell should be disabled
        self.assertFalse(agent.permissions["allow_write"])
        self.assertFalse(agent.permissions["allow_shell"])
        # Write tools should be blocked
        self.assertIn("write_file", agent._blocked_tools)
        self.assertIn("edit_file", agent._blocked_tools)

    def test_apply_phase_constraints_implementation(self):
        """IMPLEMENTATION phase should enable write + shell."""
        agent = FakeAgent()
        self.rt.apply_phase_constraints(agent, "IMPLEMENTATION")

        self.assertTrue(agent.permissions["allow_write"])
        self.assertTrue(agent.permissions["allow_shell"])
        self.assertNotIn("write_file", agent._blocked_tools)
        self.assertNotIn("bash", agent._blocked_tools)

    def test_release_phase_constraints(self):
        """release_phase_constraints should restore safe defaults."""
        agent = FakeAgent()
        self.rt.apply_phase_constraints(agent, "IMPLEMENTATION")
        self.rt.release_phase_constraints(agent)

        self.assertFalse(agent.permissions["allow_write"])
        self.assertFalse(agent.permissions["allow_shell"])
        self.assertEqual(agent._blocked_tools, [])

    def test_write_file_tool_respects_permission(self):
        """write_file handler must reject when allow_write=False."""
        from claw.agent_tools import execute_tool, ToolExecutionContext

        # Without allow_write
        ctx = ToolExecutionContext(cwd=self.tmpdir, permissions={"allow_write": False})
        result = execute_tool("write_file", {"path": os.path.join(self.tmpdir, "test.txt"), "content": "hello"}, context=ctx)
        self.assertFalse(result.ok)
        self.assertIn("permission denied", result.error.lower())

        # With allow_write
        ctx_write = ToolExecutionContext(cwd=self.tmpdir, permissions={"allow_write": True})
        result2 = execute_tool("write_file", {"path": os.path.join(self.tmpdir, "test.txt"), "content": "hello"}, context=ctx_write)
        self.assertTrue(result2.ok)

    def test_edit_file_tool_respects_permission(self):
        """edit_file handler must reject when allow_write=False."""
        from claw.agent_tools import execute_tool, ToolExecutionContext

        # Create a file first
        test_path = os.path.join(self.tmpdir, "edit_test.txt")
        with open(test_path, "w") as f:
            f.write("old content")

        # Without allow_write
        ctx = ToolExecutionContext(cwd=self.tmpdir, permissions={"allow_write": False})
        result = execute_tool("edit_file", {"path": test_path, "old_string": "old", "new_string": "new"}, context=ctx)
        self.assertFalse(result.ok)
        self.assertIn("permission denied", result.error.lower())

        # Verify file was NOT modified
        with open(test_path) as f:
            self.assertEqual(f.read(), "old content")
