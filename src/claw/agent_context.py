"""Context injection for CodeAgent."""

from __future__ import annotations

import datetime
import os
import platform
import subprocess
from typing import Any, Dict, List, Optional


def get_git_status(cwd: str) -> Dict[str, Any]:
    """Get git status information."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"branch": None, "dirty": False, "changed_files": []}

        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        return {
            "branch": branch.stdout.strip() if branch.returncode == 0 else None,
            "dirty": len(result.stdout.strip()) > 0,
            "changed_files": result.stdout.strip().split("\n") if result.stdout.strip() else [],
        }
    except Exception:
        return {"branch": None, "dirty": False, "changed_files": []}


def get_git_diff(cwd: str, max_lines: int = 100) -> str:
    """Get git diff summary."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > max_lines:
                lines = lines[:max_lines] + ["... (truncated)"]
            return "\n".join(lines)
    except Exception:
        pass
    return ""


def get_shell_info() -> Dict[str, Any]:
    """Get shell information."""
    shell = os.environ.get("SHELL", "")
    if shell:
        shell = os.path.basename(shell)
    return {
        "shell": shell,
        "prompt": os.environ.get("PS1", ""),
    }


def get_platform_info() -> Dict[str, Any]:
    """Get platform information."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


def get_claude_md_content(cwd: str) -> Optional[str]:
    """Read CLAUDE.md if present."""
    for name in ["CLAUDE.md", "CLAUDE.MD"]:
        path = os.path.join(cwd, name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    return None


def get_runtime_summaries(runtimes: List[Any]) -> List[str]:
    """Get summaries from all runtimes."""
    summaries = []
    for runtime in runtimes:
        if hasattr(runtime, "render_summary"):
            try:
                summary = runtime.render_summary()
                if summary:
                    summaries.append(summary)
            except Exception:
                pass
    return summaries


def get_user_context(
    cwd: str,
    runtimes: Optional[List[Any]] = None,
    include_claude_md: bool = True,
) -> Dict[str, Any]:
    """Build user context information.

    This collects:
    - Git status (branch, dirty state, changed files)
    - Shell information
    - Platform information
    - CLAUDE.md content if present
    - Runtime summaries from active runtimes
    """
    context: Dict[str, Any] = {
        "timestamp": datetime.datetime.now().isoformat(),
        "cwd": cwd,
        "git": get_git_status(cwd),
        "shell": get_shell_info(),
        "platform": get_platform_info(),
    }

    # Add git diff if repo is dirty
    if context["git"]["dirty"]:
        context["git"]["diff_summary"] = get_git_diff(cwd)

    # Add CLAUDE.md content
    if include_claude_md:
        claude_md = get_claude_md_content(cwd)
        if claude_md:
            context["claude_md"] = claude_md

    # Add runtime summaries
    if runtimes:
        context["runtime_summaries"] = get_runtime_summaries(runtimes)

    return context


def format_context_for_prompt(context: Dict[str, Any]) -> str:
    """Format context dict as a string for inclusion in prompts."""
    lines = ["[Environment Context]"]

    if context.get("git", {}).get("branch"):
        branch = context["git"]["branch"]
        dirty = context["git"].get("dirty", False)
        lines.append(f"Git branch: {branch}" + (" (dirty)" if dirty else ""))

    if context.get("shell", {}).get("shell"):
        lines.append(f"Shell: {context['shell']['shell']}")

    lines.append(f"Platform: {context.get('platform', {}).get('system', 'unknown')}")

    if context.get("claude_md"):
        lines.append("\n[CLAUDE.md]")
        lines.append(context["claude_md"][:500] + ("..." if len(context["claude_md"]) > 500 else ""))

    if context.get("runtime_summaries"):
        lines.append("\n[Runtime Status]")
        for summary in context["runtime_summaries"]:
            lines.append(summary)

    return "\n".join(lines)