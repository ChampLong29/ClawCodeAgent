"""Sandbox management for isolated agent execution.

`SandboxManager` is a thin per-path wrapper around `WorkspaceSandbox` that
matches the API expected by `AgentEnv` / `RolloutRunner`:

    mgr.create_sandbox(task_id, template_dir=...) -> sandbox_path
    mgr.execute_tests(sandbox_path, commands)     -> TestResult
    mgr.compute_diff(sandbox_path, files)         -> DiffResult
    mgr.cleanup(sandbox_path)                     -> None

Each call to `create_sandbox` allocates a fresh temp directory and
constructs a `WorkspaceSandbox` rooted there. The manager keeps a
``path -> WorkspaceSandbox`` map so subsequent operations can resolve
the underlying instance.

Seatbelt and git tracking are disabled by default for training sandboxes
because (a) they're already throwaway temp dirs, and (b) seatbelt is
macOS-only and git slows tests considerably. Pass a custom `SandboxConfig`
if you need them.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..sandbox import (
    SandboxConfig,
    WorkspaceSandbox,
    TestResult,
    DiffResult,
)


class SandboxManager:
    """Path-keyed registry of WorkspaceSandbox instances."""

    def __init__(self, config: Optional[SandboxConfig] = None):
        # Default to lightweight config: no seatbelt, no git overhead.
        self._config = config or SandboxConfig(
            enabled=True,
            seatbelt=False,
            git_tracking=False,
        )
        self._sandboxes: Dict[str, WorkspaceSandbox] = {}

    def create_sandbox(
        self,
        task_id: str = "",
        template_dir: Optional[str] = None,
    ) -> str:
        """Create a fresh isolated sandbox in a new temp directory.

        Returns the absolute path to the sandbox. Use that path for
        subsequent ``execute_tests`` / ``compute_diff`` / ``cleanup`` calls.
        """
        ws = WorkspaceSandbox(
            cwd=None,
            template_dir=template_dir,
            config=self._config,
        )
        self._sandboxes[ws.cwd] = ws
        return ws.cwd

    def execute_tests(
        self,
        sandbox_path: str,
        test_commands: List[str],
        timeout: float = 120.0,
    ) -> TestResult:
        return self._resolve(sandbox_path).execute_tests(test_commands, timeout=timeout)

    def compute_diff(
        self,
        sandbox_path: str,
        ground_truth_files: Dict[str, str],
    ) -> DiffResult:
        return self._resolve(sandbox_path).compute_diff(ground_truth_files)

    def cleanup(self, sandbox_path: str) -> None:
        ws = self._sandboxes.pop(sandbox_path, None)
        if ws is not None:
            ws.cleanup()

    def _resolve(self, sandbox_path: str) -> WorkspaceSandbox:
        ws = self._sandboxes.get(sandbox_path)
        if ws is None:
            # Lazy-attach to a pre-existing directory (e.g., when the
            # caller created the sandbox path themselves).
            ws = WorkspaceSandbox(cwd=sandbox_path, config=self._config)
            self._sandboxes[sandbox_path] = ws
        return ws


__all__ = ["SandboxManager", "TestResult", "DiffResult", "SandboxConfig"]
