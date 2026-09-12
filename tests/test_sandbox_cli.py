"""CLI selection tests for execution sandbox backends."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from claw.agent_types import AgentRunResult
from claw.main import main


class SandboxCLITest(unittest.TestCase):
    def test_docker_backend_requires_explicit_image(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with patch("claw.main.run_query") as run_query:
                status = main(
                    [
                        "agent",
                        "test",
                        "--cwd",
                        workspace,
                        "--sandbox-backend",
                        "docker",
                    ]
                )
        self.assertEqual(status, 2)
        run_query.assert_not_called()

    def test_agent_forwards_explicit_docker_selection(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with patch("claw.main.run_query") as run_query:
                run_query.return_value = AgentRunResult(
                    stop_reason="completed",
                    final_message="done",
                )
                status = main(
                    [
                        "agent",
                        "test",
                        "--cwd",
                        workspace,
                        "--sandbox-backend",
                        "docker",
                        "--sandbox-image",
                        "python:3.12-slim",
                    ]
                )
        self.assertEqual(status, 0)
        self.assertEqual(
            run_query.call_args.kwargs["sandbox_backend"],
            "docker",
        )
        self.assertEqual(
            run_query.call_args.kwargs["sandbox_image"],
            "python:3.12-slim",
        )

    def test_host_backend_rejects_ignored_docker_image(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with patch("claw.main.run_query") as run_query:
                status = main(
                    [
                        "agent",
                        "test",
                        "--cwd",
                        workspace,
                        "--sandbox-image",
                        "python:3.12-slim",
                    ]
                )
        self.assertEqual(status, 2)
        run_query.assert_not_called()

    def test_resume_without_override_defers_to_persisted_backend(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with patch("claw.main.run_query") as run_query:
                run_query.return_value = AgentRunResult(
                    stop_reason="completed",
                    final_message="done",
                )
                status = main(
                    [
                        "resume",
                        "continue",
                        "--session-id",
                        "session-1",
                        "--cwd",
                        workspace,
                    ]
                )

        self.assertEqual(status, 0)
        self.assertIsNone(run_query.call_args.kwargs["sandbox_backend"])
        self.assertIsNone(run_query.call_args.kwargs["sandbox_image"])


if __name__ == "__main__":
    unittest.main()
