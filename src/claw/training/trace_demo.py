"""Utilities for replaying rollout traces as demo sessions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agent_session import AgentSession
from ..session_store import save_agent_session_checkpoint


def load_rollout(path: str, index: int = 0) -> Dict[str, Any]:
    """Load one rollout row from a JSONL trace file."""
    trace_path = Path(path).expanduser()
    if not trace_path.is_file():
        raise FileNotFoundError(f"Trace file not found: {trace_path}")

    with trace_path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == index:
                return json.loads(line)

    raise IndexError(f"Trace index out of range: {index}")


def render_rollout(row: Dict[str, Any]) -> str:
    """Render a rollout row as a readable CLI transcript."""
    lines: List[str] = []
    lines.append(f"Trace: {row.get('task_id', '(unknown task)')}")
    lines.append(f"Stop: {row.get('stop_reason', '')} | Reward: {row.get('reward', 0)}")

    test_result = row.get("test_result") or {}
    diff_result = row.get("diff_result") or {}
    if test_result:
        lines.append(
            "Tests: "
            f"{test_result.get('passed_tests', 0)}/{test_result.get('total_tests', 0)} "
            f"passed={test_result.get('passed')}"
        )
    if diff_result:
        lines.append(
            "Diff: "
            f"{diff_result.get('matches', 0)}/{diff_result.get('total', 0)} "
            f"match={diff_result.get('match')}"
        )
    lines.append("")

    for msg in row.get("messages", []):
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        if role == "user":
            lines.append("USER:")
            lines.append(_indent(content))
        elif role == "assistant":
            lines.append("AGENT:")
            if content:
                lines.append(_indent(content))
            for call in msg.get("tool_calls") or []:
                name, arguments = _tool_call_parts(call)
                args_json = json.dumps(arguments, ensure_ascii=False)
                lines.append(_indent(f"[tool_call] {name} {args_json}"))
        elif role == "tool":
            name = msg.get("tool_name") or "tool"
            lines.append(f"TOOL RESULT ({name}):")
            lines.append(_indent(content))
        else:
            lines.append(f"{role.upper()}:")
            lines.append(_indent(content))
        lines.append("")

    return "\n".join(lines).rstrip()


def install_trace_session(
    row: Dict[str, Any],
    cwd: str,
    session_id: Optional[str] = None,
    mode: str = "chat",
) -> str:
    """Persist one rollout row as a normal Claw agent session."""
    task_id = str(row.get("task_id") or "trace")
    sid = session_id or _safe_session_id(f"demo-{task_id}")
    now = time.time()

    session = AgentSession(
        session_id=sid,
        messages=list(row.get("messages") or []),
        metadata={
            "tui_mode": mode,
            "source": "rollout_trace",
            "task_id": task_id,
            "reward": row.get("reward"),
            "test_result": row.get("test_result"),
            "diff_result": row.get("diff_result"),
        },
        created_at=now,
        updated_at=now,
        model="mock-model",
        stop_reason=row.get("stop_reason"),
        cwd=cwd,
        name=f"demo trace: {task_id}",
    )
    return save_agent_session_checkpoint(session, cwd)


def _indent(text: str) -> str:
    if not text:
        return "  "
    return "\n".join(f"  {line}" for line in str(text).splitlines())


def _tool_call_parts(call: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    if "name" in call:
        return str(call.get("name") or "tool"), call.get("arguments") or {}

    fn = call.get("function") or {}
    name = str(fn.get("name") or "tool")
    raw_args = fn.get("arguments") or {}
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except json.JSONDecodeError:
            raw_args = {"raw": raw_args}
    return name, raw_args if isinstance(raw_args, dict) else {"value": raw_args}


def _safe_session_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)[:80]
