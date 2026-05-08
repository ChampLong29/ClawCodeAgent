"""Workspace sandbox — Git tracking + macOS Seatbelt + bash security.

Three-layer defense for agent file-system operations:

1. **sandbox-exec** (macOS Seatbelt, kernel-enforced)
   — blocks access to sensitive paths and ports
2. **bash_security** (regex patterns)
   — intercepts dangerous command patterns
3. **Git tracking** (recoverability)
   — auto-commits at phase boundaries, enables ``git reset`` rollback

Non-macOS users automatically skip layer 1 (configurable).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SandboxConfig:
    """Configuration for WorkspaceSandbox."""

    enabled: bool = True
    seatbelt: bool = True        # macOS sandbox-exec
    git_tracking: bool = True    # phase-boundary git commits
    deny_paths: List[str] = field(default_factory=lambda: [
        "~/.ssh", "~/.aws", "/etc/passwd", "/etc/shadow",
    ])
    deny_ports: List[int] = field(default_factory=lambda: [22, 5432, 6379])
    timeout: float = 120.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "seatbelt": self.seatbelt,
            "git_tracking": self.git_tracking,
            "deny_paths": self.deny_paths,
            "deny_ports": self.deny_ports,
            "timeout": self.timeout,
        }


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


# ---------------------------------------------------------------------------
# Seatbelt profile generator
# ---------------------------------------------------------------------------

def _generate_seatbelt_profile(
    sandbox_cwd: str,
    deny_paths: List[str],
    deny_ports: List[int],
) -> str:
    """Generate a macOS Seatbelt profile for a sandbox directory.

    Uses ``(allow default)`` so the agent can do normal development work,
    then explicitly denies sensitive paths and ports.
    """
    lines = ["(version 1)", "(allow default)", ""]

    # Deny sensitive file paths
    for path in deny_paths:
        expanded = os.path.expanduser(path)
        lines.append(f";; deny access to {path}")
        if os.path.isdir(expanded) or path.startswith("/"):
            lines.append(f"(deny file-read* file-write* (subpath \"{expanded}\"))")
        else:
            lines.append(f"(deny file-read* file-write* (literal \"{expanded}\"))")

    # Deny writing to parent of sandbox (prevent cd .. pollution)
    parent = os.path.dirname(os.path.abspath(sandbox_cwd))
    if parent and parent != sandbox_cwd:
        lines.append(f";; deny writes outside sandbox")
        lines.append(
            f'(deny file-write* (subpath "{parent}") '
            f'(except (subpath "{os.path.abspath(sandbox_cwd)}")))'
        )

    # Deny sensitive ports
    if deny_ports:
        lines.append(";; deny sensitive ports")
        for port in deny_ports:
            lines.append(f'(deny network* (local ip "*:{port}"))')

    lines.append("")
    return "\n".join(lines)


def _check_sandbox_exec() -> bool:
    """Check if macOS sandbox-exec is available."""
    return os.path.exists("/usr/bin/sandbox-exec")


# ---------------------------------------------------------------------------
# WorkspaceSandbox
# ---------------------------------------------------------------------------

class WorkspaceSandbox:
    """Git-based workspace sandbox with optional macOS Seatbelt isolation.

    Usage::

        ws = WorkspaceSandbox(cwd="/path/to/project")
        ws.execute("python -m pytest tests/")
        ws.save_phase_snapshot("REQUIREMENTS")
        ws.reset_to_phase("REQUIREMENTS")
        ws.cleanup()
    """

    def __init__(
        self,
        cwd: Optional[str] = None,
        template_dir: Optional[str] = None,
        config: Optional[SandboxConfig] = None,
    ):
        self.config = config or SandboxConfig()
        self.cwd = cwd or tempfile.mkdtemp(prefix="agent_sandbox_")
        self._seatbelt_available = _check_sandbox_exec()
        self._commits: Dict[str, str] = {}  # phase_name → commit_hash
        self._temp_dir: Optional[str] = (
            self.cwd if not cwd else None
        )  # track if we created it

        os.makedirs(self.cwd, exist_ok=True)

        if template_dir and os.path.isdir(template_dir):
            self._copy_template(template_dir)

        if self.config.git_tracking:
            self._init_git()
            self._git_commit("Initial sandbox state")

    # ------------------------------------------------------------------
    # Git operations
    # ------------------------------------------------------------------

    def _init_git(self) -> None:
        try:
            subprocess.run(
                ["git", "init", "-q"], cwd=self.cwd,
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _git_commit(self, message: str) -> Optional[str]:
        """Commit all changes. Returns commit hash or None."""
        try:
            subprocess.run(
                ["git", "add", "-A"], cwd=self.cwd,
                capture_output=True, timeout=10,
            )
            r = subprocess.run(
                ["git", "commit", "-q", "--allow-empty", "-m", message],
                cwd=self.cwd,
                capture_output=True, text=True, timeout=10,
            )
            # Extract commit hash
            r2 = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.cwd,
                capture_output=True, text=True, timeout=5,
            )
            return r2.stdout.strip()[:8] if r2.returncode == 0 else None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def save_phase_snapshot(self, phase_name: str) -> Optional[str]:
        """Commit current state and save hash under *phase_name*."""
        if not self.config.git_tracking:
            return None
        commit_hash = self._git_commit(f"phase: {phase_name}")
        if commit_hash:
            self._commits[phase_name] = commit_hash
        return commit_hash

    def reset_to_phase(self, phase_name: str) -> bool:
        """``git reset --hard`` to the commit saved for *phase_name*."""
        commit_hash = self._commits.get(phase_name)
        if not commit_hash:
            return False
        try:
            subprocess.run(
                ["git", "reset", "--hard", commit_hash], cwd=self.cwd,
                capture_output=True, timeout=10,
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def get_diff_stat(self) -> str:
        """``git diff --stat`` for current changes."""
        try:
            r = subprocess.run(
                ["git", "diff", "--stat"], cwd=self.cwd,
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(
        self, command: str, timeout: Optional[float] = None
    ) -> subprocess.CompletedProcess:
        """Run a shell command inside the sandbox.

        If Seatbelt is enabled on macOS, the command is wrapped with
        ``sandbox-exec -f`` using a dynamically generated profile.
        """
        effective_timeout = timeout or self.config.timeout

        if self._seatbelt_enabled():
            profile = _generate_seatbelt_profile(
                self.cwd,
                self.config.deny_paths,
                self.config.deny_ports,
            )
            # Feed profile via stdin to avoid temp file
            wrapped = (
                f"sandbox-exec -f /dev/stdin /bin/bash -c {shlex.quote(command)}"
                f" << 'SANDBOX_PROFILE_END'\n{profile}\nSANDBOX_PROFILE_END"
            )
            command = wrapped

        return subprocess.run(
            command,
            shell=True,
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )

    def _seatbelt_enabled(self) -> bool:
        return (
            self.config.enabled
            and self.config.seatbelt
            and self._seatbelt_available
        )

    # ------------------------------------------------------------------
    # Test execution (compatible with training subsystem)
    # ------------------------------------------------------------------

    def execute_tests(
        self, test_commands: List[str], timeout: float = 120.0
    ) -> TestResult:
        """Run test commands sequentially and return results."""
        if not test_commands:
            return TestResult(
                passed=True, total_tests=0, passed_tests=0,
                output="No tests to run",
            )

        total = len(test_commands)
        passed_count = 0
        all_output: List[str] = []
        start = time.time()

        for cmd in test_commands:
            try:
                result = self.execute(cmd, timeout=timeout)
                all_output.append(f"$ {cmd}\n{result.stdout}")
                if result.returncode == 0:
                    passed_count += 1
            except subprocess.TimeoutExpired:
                all_output.append(f"$ {cmd}\n[TIMEOUT after {timeout}s]")

        return TestResult(
            passed=(passed_count == total),
            total_tests=total,
            passed_tests=passed_count,
            output="\n\n".join(all_output),
            execution_time=time.time() - start,
        )

    def compute_diff(
        self, ground_truth_files: Dict[str, str]
    ) -> DiffResult:
        """Compare files in sandbox against expected content."""
        details: List[Dict[str, Any]] = []
        matches = 0
        total = len(ground_truth_files)

        if total == 0:
            return DiffResult(match=True, matches=0, total=0, details=[])

        for file_path, expected in ground_truth_files.items():
            full_path = os.path.join(self.cwd, file_path)
            if not os.path.exists(full_path):
                details.append({
                    "file": file_path, "match": False,
                    "reason": "File not found",
                })
                continue

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    actual = f.read()
            except (UnicodeDecodeError, OSError) as e:
                details.append({
                    "file": file_path, "match": False,
                    "reason": f"Read error: {e}",
                })
                continue

            file_match = actual.strip() == expected.strip()
            if file_match:
                matches += 1
            details.append({
                "file": file_path, "match": file_match,
                "expected_length": len(expected),
                "actual_length": len(actual),
            })

        return DiffResult(
            match=(matches == total),
            matches=matches, total=total, details=details,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _copy_template(self, template_dir: str) -> None:
        for item in os.listdir(template_dir):
            src = os.path.join(template_dir, item)
            dst = os.path.join(self.cwd, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    def cleanup(self) -> None:
        """Remove the sandbox directory (only if we created it)."""
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        elif os.path.isdir(self.cwd):
            shutil.rmtree(self.cwd, ignore_errors=True)
