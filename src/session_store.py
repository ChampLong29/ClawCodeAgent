"""Session persistence for CodeAgent."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .agent_session import AgentSession


def _get_sessions_dir(base_path: str) -> str:
    """Get the sessions directory path."""
    return os.path.join(base_path, ".port_sessions", "agent")


def save_agent_session(session: AgentSession, base_path: str) -> str:
    """Save an agent session to disk.

    Returns the path to the saved session file.
    """
    sessions_dir = _get_sessions_dir(base_path)
    os.makedirs(sessions_dir, exist_ok=True)

    filepath = os.path.join(sessions_dir, f"{session.session_id}.json")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(session.to_dict(), f, indent=2)

    return filepath


def load_agent_session(session_id: str, base_path: str) -> AgentSession:
    """Load an agent session from disk."""
    filepath = os.path.join(_get_sessions_dir(base_path), f"{session_id}.json")

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Session not found: {session_id}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    return AgentSession.from_dict(data)


def list_sessions(base_path: str) -> list[Dict[str, Any]]:
    """List all saved sessions."""
    sessions_dir = _get_sessions_dir(base_path)

    if not os.path.exists(sessions_dir):
        return []

    sessions = []
    for filename in os.listdir(sessions_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(sessions_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    sessions.append({
                        "session_id": data.get("session_id"),
                        "name": data.get("name"),
                        "created_at": data.get("created_at"),
                        "updated_at": data.get("updated_at"),
                        "message_count": len(data.get("messages", [])),
                        "model": data.get("model"),
                        "stop_reason": data.get("stop_reason"),
                        "cwd": data.get("cwd"),
                    })
            except (json.JSONDecodeError, OSError):
                continue

    return sessions


def delete_agent_session(session_id: str, base_path: str) -> bool:
    """Delete a session file."""
    filepath = os.path.join(_get_sessions_dir(base_path), f"{session_id}.json")
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


def load_session_by_name(name: str, base_path: str) -> Optional[AgentSession]:
    """Load a session by its name field. Returns None if not found."""
    sessions_dir = _get_sessions_dir(base_path)
    if not os.path.exists(sessions_dir):
        return None

    for filename in os.listdir(sessions_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(sessions_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("name") == name:
                        return AgentSession.from_dict(data)
            except (json.JSONDecodeError, OSError):
                continue
    return None


def list_sessions_by_prefix(prefix: str, base_path: str) -> List[Dict[str, Any]]:
    """List sessions whose name starts with the given prefix."""
    sessions_dir = _get_sessions_dir(base_path)
    if not os.path.exists(sessions_dir):
        return []

    sessions = []
    for filename in os.listdir(sessions_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(sessions_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    name = data.get("name", "")
                    if name.startswith(prefix):
                        sessions.append({
                            "session_id": data.get("session_id"),
                            "name": name,
                            "created_at": data.get("created_at"),
                            "updated_at": data.get("updated_at"),
                            "message_count": len(data.get("messages", [])),
                            "model": data.get("model"),
                            "stop_reason": data.get("stop_reason"),
                            "cwd": data.get("cwd"),
                        })
            except (json.JSONDecodeError, OSError):
                continue

    sessions.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
    return sessions