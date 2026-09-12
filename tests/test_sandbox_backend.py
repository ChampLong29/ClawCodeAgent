"""Contract and security regression tests for sandbox backends."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from claw.agent_session import AgentSession
from claw.agent_tools import (
    ToolExecutionContext,
    _virtual_tool_handler,
    execute_tool,
    execute_tool_streaming,
)
from claw.agent_runtime import LocalCodingAgent
from claw.sandbox_backend import (
    EnvironmentSpec,
    ExecRequest,
    HostBackend,
    NetworkMode,
    NetworkPolicy,
    ResourceLimits,
    SandboxBackendError,
    SandboxErrorCode,
    SandboxRuntimeTier,
    SandboxSpec,
    SandboxState,
)


class HostBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_context = tempfile.TemporaryDirectory()
        self.workspace = self.workspace_context.name
        self.backend = HostBackend()
        self.spec = SandboxSpec.for_host_workspace(
            self.workspace,
            owner_id="test-session",
            sandbox_id="host-test-session",
        )

    def tearDown(self) -> None:
        self.workspace_context.cleanup()

    def _running_handle(self):
        handle = self.backend.prepare(self.spec)
        self.backend.start(handle)
        return handle

    def test_capabilities_do_not_claim_os_isolation(self) -> None:
        capabilities = self.backend.capabilities()
        self.assertEqual(capabilities.isolation_tiers, (SandboxRuntimeTier.HOST,))
        self.assertEqual(capabilities.network_modes, (NetworkMode.UNRESTRICTED,))
        self.assertFalse(capabilities.supports_resource_limits)
        self.assertEqual(capabilities.supports_streaming_exec, os.name == "posix")
        self.assertFalse(capabilities.admission_enforced_out_of_process)

    def test_spec_fingerprint_is_deterministic(self) -> None:
        equivalent = SandboxSpec.for_host_workspace(
            self.workspace,
            owner_id="test-session",
            sandbox_id="host-test-session",
        )
        self.assertEqual(self.spec.fingerprint(), equivalent.fingerprint())

    def test_lifecycle_and_owner_listing(self) -> None:
        handle = self.backend.prepare(self.spec)
        self.assertEqual(self.backend.inspect(handle).state, SandboxState.READY)
        self.backend.start(handle)
        self.assertEqual(self.backend.inspect(handle).state, SandboxState.RUNNING)
        self.assertEqual(len(self.backend.list_owned("test-session")), 1)
        self.backend.stop(handle)
        self.assertEqual(self.backend.inspect(handle).state, SandboxState.STOPPED)
        self.backend.destroy(handle)
        self.assertEqual(self.backend.inspect(handle).state, SandboxState.DESTROYED)
        replacement = self.backend.prepare(self.spec)
        self.assertEqual(replacement.generation, 2)

    def test_exec_uses_minimal_environment(self) -> None:
        handle = self._running_handle()
        command = (
            sys.executable,
            "-c",
            (
                "import json, os; print(json.dumps({"
                "'secret': os.getenv('CLAW_TEST_API_KEY'), "
                "'home': os.getenv('HOME'), 'path': os.getenv('PATH')}))"
            ),
        )
        with patch.dict(
            os.environ,
            {
                "CLAW_TEST_API_KEY": "must-not-leak",
                "GITHUB_TOKEN": "also-must-not-leak",
                "PATH": "/tmp/rogue-bin",
            },
        ):
            result = self.backend.exec(handle, ExecRequest(command=command))
        payload = json.loads(result.stdout)
        self.assertTrue(result.ok)
        self.assertIsNone(payload["secret"])
        self.assertEqual(payload["home"], os.path.realpath(self.workspace))
        self.assertTrue(payload["path"])
        self.assertNotIn("/tmp/rogue-bin", payload["path"])

    def test_sensitive_explicit_environment_is_denied(self) -> None:
        spec = SandboxSpec(
            **{
                **self.spec.__dict__,
                "environment": EnvironmentSpec(values={"SERVICE_API_KEY": "secret"}),
            }
        )
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.prepare(spec)
        self.assertEqual(raised.exception.code, SandboxErrorCode.DENIED_BY_POLICY)

        inherited_secret = SandboxSpec(
            **{
                **self.spec.__dict__,
                "sandbox_id": "host-inherited-secret-test",
                "environment": EnvironmentSpec(
                    inherited_names=("GITHUB_TOKEN",)
                ),
            }
        )
        inherited_handle = self.backend.prepare(inherited_secret)
        self.backend.start(inherited_handle)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "must-not-leak"}):
            result = self.backend.exec(
                inherited_handle,
                ExecRequest(
                    command=(
                        sys.executable,
                        "-c",
                        "import os; print(os.getenv('GITHUB_TOKEN'))",
                    )
                ),
            )
        self.assertEqual(result.stdout.strip(), "None")

    def test_backend_managed_environment_cannot_be_overridden(self) -> None:
        spec = SandboxSpec(
            **{
                **self.spec.__dict__,
                "environment": EnvironmentSpec(values={"HOME": "/tmp/escape"}),
            }
        )
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.prepare(spec)
        self.assertEqual(raised.exception.code, SandboxErrorCode.DENIED_BY_POLICY)

    def test_unsupported_controls_fail_closed(self) -> None:
        limited = SandboxSpec(
            **{
                **self.spec.__dict__,
                "resources": ResourceLimits(memory_mb=128),
            }
        )
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.prepare(limited)
        self.assertEqual(
            raised.exception.code, SandboxErrorCode.CAPABILITY_UNAVAILABLE
        )

        isolated_network = SandboxSpec(
            **{
                **self.spec.__dict__,
                "sandbox_id": "host-network-test",
                "network": NetworkPolicy(mode=NetworkMode.NONE),
            }
        )
        with self.assertRaises(SandboxBackendError):
            self.backend.prepare(isolated_network)

    def test_exec_cwd_cannot_escape_workspace(self) -> None:
        handle = self._running_handle()
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.exec(
                handle,
                ExecRequest(command=(sys.executable, "-V"), cwd=".."),
            )
        self.assertEqual(raised.exception.code, SandboxErrorCode.DENIED_BY_POLICY)

    def test_timeout_and_output_limit_are_structured(self) -> None:
        handle = self._running_handle()
        timeout = self.backend.exec(
            handle,
            ExecRequest(
                command=(sys.executable, "-c", "import time; time.sleep(2)"),
                timeout_seconds=0.05,
            ),
        )
        self.assertFalse(timeout.ok)
        self.assertTrue(timeout.timed_out)
        self.assertIsNone(timeout.returncode)
        self.assertEqual(timeout.error_code, SandboxErrorCode.EXEC_TIMEOUT)

        limited = self.backend.exec(
            handle,
            ExecRequest(
                command=(sys.executable, "-c", "print('abcdefghij', end='')"),
                output_limit_bytes=4,
            ),
        )
        self.assertEqual(limited.stdout, "abcd")
        self.assertTrue(limited.output_truncated)

    def test_streaming_handles_output_without_newline_and_limits_it(self) -> None:
        handle = self._running_handle()
        events = list(
            self.backend.stream_exec(
                handle,
                ExecRequest(
                    command=(
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.write('abcdefghij'); sys.stdout.flush()",
                    ),
                    output_limit_bytes=4,
                ),
            )
        )
        self.assertEqual("".join(event.stdout for event in events), "abcd")
        self.assertIsNotNone(events[-1].result)
        self.assertTrue(events[-1].result.output_truncated)


class ToolSandboxRoutingTest(unittest.TestCase):
    def test_streaming_shell_requires_permission_for_safe_commands(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            events = list(
                execute_tool_streaming(
                    "bash",
                    {"command": "pwd"},
                    ToolExecutionContext(
                        cwd=workspace,
                        permissions={"allow_shell": False},
                    ),
                )
            )
        self.assertFalse(events[-1]["ok"])
        self.assertIn("Shell access not permitted", events[-1]["error"])

    def test_virtual_command_requires_shell_permission(self) -> None:
        result = _virtual_tool_handler(
            "plugin-command",
            {"command": "pwd"},
            {"permissions": {"allow_shell": False}},
        )
        self.assertFalse(result["ok"])
        self.assertIn("Shell access not permitted", result["error"])

    def test_bash_routes_through_supplied_backend(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            backend = HostBackend()
            spec = SandboxSpec.for_host_workspace(
                workspace,
                owner_id="tool-test",
                sandbox_id="tool-test",
            )
            handle = backend.prepare(spec)
            backend.start(handle)
            command = f"{shlex.quote(sys.executable)} -c \"print('through-backend')\""
            result = execute_tool(
                "bash",
                {"command": command},
                ToolExecutionContext(
                    cwd=workspace,
                    permissions={"allow_shell": True},
                    sandbox_backend=backend,
                    sandbox_handle=handle,
                ),
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.result["backend_name"], "host")
        self.assertEqual(result.result["sandbox_id"], "tool-test")
        self.assertIn("through-backend", result.result["stdout"])

    def test_agent_reuses_session_owned_handle(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            agent = LocalCodingAgent(cwd=workspace)
            agent.session = AgentSession(session_id="persistent-sandbox")
            first = agent._tool_execution_context({"allow_shell": True})
            second = agent._tool_execution_context({"allow_shell": True})
            self.assertIs(first.sandbox_handle, second.sandbox_handle)
            self.assertEqual(
                agent.session.metadata["sandbox"]["sandbox_id"],
                "host-session-persistent-sandbox",
            )


if __name__ == "__main__":
    unittest.main()
