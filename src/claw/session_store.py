"""Session persistence for CodeAgent.

Uses JSONL format (one metadata or entry object per line) for incremental
appends. Legacy raw-message JSONL and single-object JSON remain readable.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .agent_session import AgentSession, SessionEntry


def _get_sessions_dir(base_path: str) -> str:
    return os.path.join(base_path, ".port_sessions", "agent")


# ---------------------------------------------------------------------------
# Save / Load — JSONL incremental + checkpoint
# ---------------------------------------------------------------------------

def save_agent_session(session: AgentSession, base_path: str) -> str:
    """Append new tree entries to the session file since the last save.

    Uses JSONL format: each v2 tree entry is one line. A full checkpoint is
    written for a new file or when an older format is migrated.
    """
    sessions_dir = _get_sessions_dir(base_path)
    os.makedirs(sessions_dir, exist_ok=True)

    filepath = os.path.join(sessions_dir, f"{session.session_id}.jsonl")
    last_count = int(session.metadata.get("last_saved_entry_count", 0))
    total = len(session.entries)

    if total < last_count or last_count == 0 or not os.path.exists(filepath):
        _write_checkpoint(session, filepath)
        _mark_saved(session)
        return filepath

    new_entries = session.entries[last_count:]
    with open(filepath, "a", encoding="utf-8") as f:
        # Appending metadata keeps mutable fields such as stop_reason and name
        # current while preserving the append-only history records.
        f.write(json.dumps(_metadata_record(session), ensure_ascii=False) + "\n")
        for entry in new_entries:
            f.write(json.dumps(_entry_record(entry), ensure_ascii=False) + "\n")

    _mark_saved(session)
    return filepath


def save_agent_session_checkpoint(session: AgentSession, base_path: str) -> str:
    """Force a full v2 checkpoint write."""
    sessions_dir = _get_sessions_dir(base_path)
    os.makedirs(sessions_dir, exist_ok=True)
    filepath = os.path.join(sessions_dir, f"{session.session_id}.jsonl")
    _write_checkpoint(session, filepath)
    _mark_saved(session)
    return filepath


def _write_checkpoint(session: AgentSession, filepath: str) -> None:
    """Write the complete session as a single checkpoint.

    Also writes a human-readable metadata header as a JSON object with
    ``__meta__: true`` marker.
    """
    meta = _metadata_record(session)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for entry in session.entries:
            f.write(json.dumps(_entry_record(entry), ensure_ascii=False) + "\n")


def _metadata_record(session: AgentSession) -> Dict[str, Any]:
    return {
        "__meta__": True,
        "schema_version": session.schema_version,
        "session_id": session.session_id,
        "name": session.name,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "model": session.model,
        "stop_reason": session.stop_reason,
        "cwd": session.cwd,
        "phase_boundaries": session.phase_boundaries,
        "current_entry_id": session.current_entry_id,
        "metadata": session.metadata,
    }


def _entry_record(entry: SessionEntry) -> Dict[str, Any]:
    record = entry.to_dict()
    record["__entry__"] = True
    return record


def _mark_saved(session: AgentSession) -> None:
    session.metadata["last_saved_entry_count"] = len(session.entries)
    # Retain the old counter for callers that display or inspect it.
    session.metadata["last_saved_count"] = len(session.messages)


def load_agent_session(session_id: str, base_path: str) -> AgentSession:
    """Load an agent session from disk.

    Tries JSONL first, falls back to legacy JSON format.
    """
    sessions_dir = _get_sessions_dir(base_path)

    # Try JSONL
    jsonl_path = os.path.join(sessions_dir, f"{session_id}.jsonl")
    if os.path.exists(jsonl_path):
        return _load_from_jsonl(jsonl_path)

    # Fallback: legacy JSON
    json_path = os.path.join(sessions_dir, f"{session_id}.json")
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Session not found: {session_id}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return AgentSession.from_dict(data)


def _load_from_jsonl(filepath: str) -> AgentSession:
    """Reconstruct a session from a JSONL file."""
    messages: List[Dict[str, Any]] = []
    entries: List[SessionEntry] = []
    meta: Dict[str, Any] = {}

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("__meta__"):
                meta = obj
            elif obj.get("__entry__"):
                entries.append(SessionEntry.from_dict(obj))
            else:
                messages.append(obj)

    session = AgentSession(
        session_id=meta.get("session_id", ""),
        messages=messages if not entries else [],
        entries=entries,
        current_entry_id=meta.get("current_entry_id"),
        schema_version=(
            meta.get("schema_version", "agent_session.v2")
            if entries
            else "agent_session.v2"
        ),
        metadata=meta.get("metadata", {}),
        created_at=meta.get("created_at"),
        updated_at=meta.get("updated_at"),
        model=meta.get("model"),
        stop_reason=meta.get("stop_reason"),
        cwd=meta.get("cwd"),
        name=meta.get("name"),
        phase_boundaries=meta.get("phase_boundaries", {}),
    )
    if entries:
        _mark_saved(session)
    else:
        # Force the next save to rewrite a legacy raw-message JSONL file as
        # v2 entry records with stable IDs.
        session.metadata["last_saved_entry_count"] = 0
        session.metadata["last_saved_count"] = len(messages)
    return session


# ---------------------------------------------------------------------------
# List / Delete
# ---------------------------------------------------------------------------

def list_sessions(base_path: str) -> list[Dict[str, Any]]:
    """List all saved sessions with metadata."""
    sessions_dir = _get_sessions_dir(base_path)
    if not os.path.exists(sessions_dir):
        return []

    sessions = []
    for filename in sorted(os.listdir(sessions_dir)):
        if not (filename.endswith(".jsonl") or filename.endswith(".json")):
            continue
        filepath = os.path.join(sessions_dir, filename)
        try:
            meta = _read_session_meta(filepath)
            if meta:
                sessions.append(meta)
        except (json.JSONDecodeError, OSError):
            continue

    sessions.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
    return sessions


def _read_session_meta(filepath: str) -> Optional[Dict[str, Any]]:
    """Read session metadata without loading all messages."""
    sid = os.path.splitext(os.path.basename(filepath))[0]

    # JSONL may contain later metadata snapshots after incremental appends.
    if filepath.endswith(".jsonl"):
        obj: Dict[str, Any] = {}
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if candidate.get("__meta__"):
                    obj = candidate
        if obj:
            msg_count = _count_jsonl_messages(filepath)
            nested_meta = obj.get("metadata", {})
            tui_mode = nested_meta.get("tui_mode") if isinstance(nested_meta, dict) else None
            return {
                "session_id": obj.get("session_id", sid),
                "name": obj.get("name"),
                "created_at": obj.get("created_at"),
                "updated_at": obj.get("updated_at"),
                "message_count": msg_count,
                "model": obj.get("model"),
                "stop_reason": obj.get("stop_reason"),
                "cwd": obj.get("cwd"),
                "tui_mode": tui_mode,
            }
        return {
            "session_id": sid,
            "message_count": _count_jsonl_messages(filepath),
        }

    # Legacy JSON
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    nested_meta = data.get("metadata", {})
    tui_mode = nested_meta.get("tui_mode") if isinstance(nested_meta, dict) else None
    return {
        "session_id": data.get("session_id", sid),
        "name": data.get("name"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "message_count": len(data.get("messages", [])),
        "model": data.get("model"),
        "stop_reason": data.get("stop_reason"),
        "cwd": data.get("cwd"),
        "tui_mode": tui_mode,
    }


def _count_jsonl_messages(filepath: str) -> int:
    """Count message entries in v2 files and raw messages in legacy files."""
    count = 0
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("__entry__"):
                        if obj.get("entry_type") == "message":
                            count += 1
                    elif not obj.get("__meta__"):
                        count += 1
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return count


def delete_agent_session(session_id: str, base_path: str) -> bool:
    """Delete a session file (tries both JSONL and JSON)."""
    sessions_dir = _get_sessions_dir(base_path)
    deleted = False
    for ext in (".jsonl", ".json"):
        filepath = os.path.join(sessions_dir, f"{session_id}{ext}")
        if os.path.exists(filepath):
            os.remove(filepath)
            deleted = True
    return deleted


def load_session_by_name(name: str, base_path: str) -> Optional[AgentSession]:
    """Load a session by its name field."""
    sessions_dir = _get_sessions_dir(base_path)
    if not os.path.exists(sessions_dir):
        return None

    for filename in sorted(os.listdir(sessions_dir)):
        if not (filename.endswith(".jsonl") or filename.endswith(".json")):
            continue
        filepath = os.path.join(sessions_dir, filename)
        try:
            meta = _read_session_meta(filepath)
            if meta and meta.get("name") == name:
                sid = meta["session_id"]
                return load_agent_session(sid, base_path)
        except (json.JSONDecodeError, OSError):
            continue
    return None


def list_sessions_by_prefix(prefix: str, base_path: str) -> List[Dict[str, Any]]:
    """List sessions whose id or name starts with the given prefix."""
    sessions = list_sessions(base_path)
    return [
        s for s in sessions
        if (s.get("session_id") or "").startswith(prefix)
        or (s.get("name") or "").startswith(prefix)
    ]
