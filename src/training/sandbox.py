"""Sandbox management for isolated agent execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TestResult:
    """Result of running test commands in a sandbox."""
    passed: bool
    total_tests: int
    passed_tests: int
    output: str
    stderr: str = ""
    execution_time: float = 0.0


@dataclass
class DiffResult:
    """Result of comparing implementation against ground truth."""
    match: bool
    matches: int
    total: int
    details: List[Dict[str, Any]]


class SandboxManager:
    """Manages isolated sandbox directories for agent execution.

    Each episode gets a fresh sandbox copied from a template (or empty dir).
    After the episode, the sandbox is cleaned up.
    """

    def __init__(
        self,
        base_dir: Optional[str] = None,
        default_timeout: float = 120.0,
        default_max_memory_mb: int = 512,
    ):
        self.base_dir = base_dir or tempfile.mkdtemp(prefix="agent_sandboxes_")
        self._active_sandboxes: Dict[str, str] = {}
        self.default_timeout = default_timeout
        self.default_max_memory_mb = default_max_memory_mb

    def create_sandbox(
        self,
        task_id: str,
        template_dir: Optional[str] = None,
    ) -> str:
        """Create an isolated sandbox directory for a task.

        If a template_dir is provided, its contents are copied into the
        sandbox as the starting state. Otherwise an empty directory is used.
        The sandbox is also initialized as a git repository for diff tracking.

        Returns the absolute path to the sandbox directory.
        """
        sandbox_path = os.path.join(self.base_dir, f"{task_id}_{int(time.time() * 1000)}")
        os.makedirs(sandbox_path, exist_ok=True)

        if template_dir and os.path.isdir(template_dir):
            # Copy template contents
            for item in os.listdir(template_dir):
                src = os.path.join(template_dir, item)
                dst = os.path.join(sandbox_path, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

        # Initialize git repository for diff tracking
        try:
            subprocess.run(
                ["git", "init", "-q"],
                cwd=sandbox_path,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "add", "-A"],
                cwd=sandbox_path,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-q", "-m", "Initial template state"],
                cwd=sandbox_path,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # git may not be available

        self._active_sandboxes[task_id] = sandbox_path
        return sandbox_path

    def execute_tests(
        self,
        sandbox_path: str,
        test_commands: List[str],
        timeout: float = 120.0,
    ) -> TestResult:
        """Run test commands in the sandbox and return results.

        Each command is run sequentially. If any command fails (non-zero exit),
        subsequent commands are still run but the overall result is FAIL.
        """
        if not test_commands:
            return TestResult(passed=True, total_tests=0, passed_tests=0, output="No tests to run")

        total = len(test_commands)
        passed_count = 0
        all_output = []
        all_stderr = []
        start = time.time()

        for cmd in test_commands:
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=sandbox_path,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                all_output.append(f"$ {cmd}\n{result.stdout}")
                if result.stderr:
                    all_stderr.append(result.stderr)
                if result.returncode == 0:
                    passed_count += 1
            except subprocess.TimeoutExpired:
                all_output.append(f"$ {cmd}\n[TIMEOUT after {timeout}s]")

        elapsed = time.time() - start
        return TestResult(
            passed=(passed_count == total),
            total_tests=total,
            passed_tests=passed_count,
            output="\n\n".join(all_output),
            stderr="\n".join(all_stderr),
            execution_time=elapsed,
        )

    def compute_diff(
        self,
        sandbox_path: str,
        ground_truth_files: Dict[str, str],
    ) -> DiffResult:
        """Compare files in the sandbox against ground truth content.

        For each file path in ground_truth_files, checks if the file exists
        in the sandbox and has the expected content.
        """
        details = []
        matches = 0
        total = len(ground_truth_files) if ground_truth_files else 0

        if not ground_truth_files:
            return DiffResult(match=True, matches=0, total=0, details=[])

        for file_path, expected_content in ground_truth_files.items():
            full_path = os.path.join(sandbox_path, file_path)
            if not os.path.exists(full_path):
                details.append({
                    "file": file_path,
                    "match": False,
                    "reason": "File not found",
                })
                continue

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    actual = f.read()
            except (UnicodeDecodeError, OSError) as e:
                details.append({
                    "file": file_path,
                    "match": False,
                    "reason": f"Read error: {e}",
                })
                continue

            file_match = actual.strip() == expected_content.strip()
            if file_match:
                matches += 1
            details.append({
                "file": file_path,
                "match": file_match,
                "expected_length": len(expected_content),
                "actual_length": len(actual),
            })

        return DiffResult(
            match=(matches == total),
            matches=matches,
            total=total,
            details=details,
        )

    def cleanup(self, sandbox_path: str) -> None:
        """Remove a sandbox directory and all its contents."""
        if os.path.isdir(sandbox_path):
            shutil.rmtree(sandbox_path, ignore_errors=True)

    def cleanup_all(self) -> None:
        """Clean up all active sandboxes."""
        for path in list(self._active_sandboxes.values()):
            self.cleanup(path)
        self._active_sandboxes.clear()

    def get_sandbox(self, task_id: str) -> Optional[str]:
        """Get the sandbox path for a task."""
        return self._active_sandboxes.get(task_id)

    def close(self) -> None:
        """Clean up all sandboxes and the base directory."""
        self.cleanup_all()
        if os.path.isdir(self.base_dir):
            shutil.rmtree(self.base_dir, ignore_errors=True)
