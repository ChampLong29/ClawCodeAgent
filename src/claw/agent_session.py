"""Agent session management with backward-compatible tree history."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agent_types import ToolCall


@dataclass
class SessionEntry:
    """One append-only node in a session history tree."""

    entry_id: str
    parent_id: Optional[str]
    entry_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    label: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "parent_id": self.parent_id,
            "entry_type": self.entry_type,
            "payload": self.payload,
            "created_at": self.created_at,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionEntry":
        return cls(
            entry_id=str(data.get("entry_id") or uuid.uuid4().hex),
            parent_id=data.get("parent_id"),
            entry_type=str(data.get("entry_type", "message")),
            payload=dict(data.get("payload", {})),
            created_at=float(data.get("created_at", time.time())),
            label=data.get("label"),
        )


@dataclass
class AgentSession:
    """Agent conversation session backed by an append-only entry tree.

    ``messages`` remains the active-branch compatibility view used by the model
    loop and older callers. Navigating to an older entry and appending creates
    an in-place branch without deleting the abandoned descendants.
    """

    session_id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    cwd: Optional[str] = None
    name: Optional[str] = None
    phase_boundaries: Dict[str, int] = field(default_factory=dict)
    entries: List[SessionEntry] = field(default_factory=list)
    current_entry_id: Optional[str] = None
    schema_version: str = "agent_session.v2"

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = time.time()
        if self.updated_at is None:
            self.updated_at = time.time()
        self.entries = [
            item if isinstance(item, SessionEntry) else SessionEntry.from_dict(item)
            for item in self.entries
        ]
        labels = self.metadata.get("entry_labels", {})
        if isinstance(labels, dict):
            for entry in self.entries:
                if entry.entry_id in labels:
                    entry.label = labels[entry.entry_id]
        if self.entries:
            known_ids = {entry.entry_id for entry in self.entries}
            if self.current_entry_id not in known_ids:
                self.current_entry_id = self.entries[-1].entry_id
            self.messages = self.get_branch_messages()
            self._rebuild_phase_boundaries()
        elif self.messages:
            legacy_messages = list(self.messages)
            self.messages = []
            for message in legacy_messages:
                self._append_message(dict(message), touch=False)
            self._rebuild_phase_boundaries()

    def _append_entry(
        self,
        entry_type: str,
        payload: Dict[str, Any],
        *,
        label: Optional[str] = None,
        touch: bool = True,
    ) -> SessionEntry:
        entry = SessionEntry(
            entry_id=uuid.uuid4().hex,
            parent_id=self.current_entry_id,
            entry_type=entry_type,
            payload=payload,
            label=label,
        )
        self.entries.append(entry)
        self.current_entry_id = entry.entry_id
        if touch:
            self.updated_at = time.time()
        return entry

    def _append_message(self, message: Dict[str, Any], *, touch: bool = True) -> None:
        self.messages.append(message)
        self._append_entry("message", {"message": message}, touch=touch)

    def add_user_message(self, content: str) -> None:
        self._append_message({"role": "user", "content": content})

    def add_assistant_message(
        self,
        content: Optional[str] = None,
        tool_calls: Optional[List[ToolCall]] = None,
        thinking: Optional[str] = None,
        thinking_signature: Optional[str] = None,
    ) -> None:
        msg: Dict[str, Any] = {"role": "assistant"}
        if content:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = [tc.to_dict() for tc in tool_calls]
        if thinking:
            msg["_thinking"] = thinking
        if thinking_signature:
            msg["_thinking_signature"] = thinking_signature
        self._append_message(msg)

    def add_tool_message(
        self,
        tool_call_id: str,
        content: str,
        tool_name: Optional[str] = None,
    ) -> None:
        msg: Dict[str, Any] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
        if tool_name:
            msg["tool_name"] = tool_name
        self._append_message(msg)

    def add_system_message(self, content: str) -> None:
        self._append_message({"role": "system", "content": content})

    def append_compaction(
        self,
        summary: str,
        *,
        first_kept_entry_id: Optional[str],
        tokens_before: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append structured compaction metadata without rewriting history."""
        entry = self._append_entry(
            "compaction",
            {
                "summary": summary,
                "first_kept_entry_id": first_kept_entry_id,
                "tokens_before": int(tokens_before),
                "details": dict(details or {}),
            },
        )
        return entry.entry_id

    def mark_phase_boundary(self, phase_name: str) -> None:
        boundary_msg = {
            "role": "system",
            "content": f"[PHASE_BOUNDARY:{phase_name}]",
            "metadata": {"phase_boundary": True, "phase_name": phase_name},
        }
        self._append_message(boundary_msg)
        self.phase_boundaries[phase_name] = len(self.messages) - 1

    def get_phase_messages(self, phase_name: str) -> List[Dict[str, Any]]:
        start_idx = self.phase_boundaries.get(phase_name)
        if start_idx is None:
            return []
        boundary_indices = sorted(self.phase_boundaries.values())
        try:
            pos = boundary_indices.index(start_idx)
            end_idx = (
                boundary_indices[pos + 1]
                if pos + 1 < len(boundary_indices)
                else len(self.messages)
            )
        except ValueError:
            end_idx = len(self.messages)
        return self.messages[start_idx:end_idx]

    def get_messages(self) -> List[Dict[str, Any]]:
        return self.messages.copy()

    def get_entries(self) -> List[SessionEntry]:
        return list(self.entries)

    def get_entry(self, entry_id: str) -> Optional[SessionEntry]:
        return next((entry for entry in self.entries if entry.entry_id == entry_id), None)

    def get_branch_entries(
        self,
        entry_id: Optional[str] = None,
    ) -> List[SessionEntry]:
        target_id = entry_id if entry_id is not None else self.current_entry_id
        if target_id is None:
            return []
        by_id = {entry.entry_id: entry for entry in self.entries}
        branch: List[SessionEntry] = []
        seen = set()
        while target_id is not None:
            if target_id in seen:
                raise ValueError("session entry graph contains a cycle")
            seen.add(target_id)
            entry = by_id.get(target_id)
            if entry is None:
                raise ValueError(f"unknown session entry: {target_id}")
            branch.append(entry)
            target_id = entry.parent_id
        branch.reverse()
        return branch

    def get_branch_messages(
        self,
        entry_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        for entry in self.get_branch_entries(entry_id):
            if entry.entry_type != "message":
                continue
            message = entry.payload.get("message")
            if isinstance(message, dict):
                messages.append(message)
        return messages

    def active_message_entry_ids(self) -> List[str]:
        return [
            entry.entry_id
            for entry in self.get_branch_entries()
            if entry.entry_type == "message"
        ]

    def first_active_entry_id_for_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Return the earliest active entry represented in ``messages``.

        Matching proceeds backwards so repeated messages map to their latest
        corresponding entries. Runtime-only guidance has no match and is
        skipped.
        """
        active_entries = [
            entry
            for entry in self.get_branch_entries()
            if entry.entry_type == "message"
            and isinstance(entry.payload.get("message"), dict)
        ]
        cursor = len(active_entries) - 1
        matched: List[tuple[int, str]] = []
        for message_index in range(len(messages) - 1, -1, -1):
            target = _message_key(messages[message_index])
            found_index: Optional[int] = None
            for entry_index in range(cursor, -1, -1):
                entry = active_entries[entry_index]
                if _message_key(entry.payload["message"]) == target:
                    matched.append((message_index, entry.entry_id))
                    found_index = entry_index
                    break
            if found_index is not None:
                cursor = found_index - 1
        if not matched:
            return None
        return min(matched, key=lambda item: item[0])[1]

    def navigate_to(self, entry_id: Optional[str]) -> None:
        """Move the active leaf; the next append creates a branch."""
        if entry_id is not None and self.get_entry(entry_id) is None:
            raise ValueError(f"unknown session entry: {entry_id}")
        self.current_entry_id = entry_id
        self.messages = self.get_branch_messages(entry_id)
        self._rebuild_phase_boundaries()
        self.updated_at = time.time()

    def label_entry(self, entry_id: str, label: Optional[str]) -> None:
        entry = self.get_entry(entry_id)
        if entry is None:
            raise ValueError(f"unknown session entry: {entry_id}")
        entry.label = label
        labels = self.metadata.setdefault("entry_labels", {})
        if isinstance(labels, dict):
            if label is None:
                labels.pop(entry_id, None)
            else:
                labels[entry_id] = label
        self.updated_at = time.time()

    def _rebuild_phase_boundaries(self) -> None:
        self.phase_boundaries = {}
        for index, message in enumerate(self.messages):
            metadata = message.get("metadata", {})
            if isinstance(metadata, dict) and metadata.get("phase_boundary"):
                name = metadata.get("phase_name")
                if name:
                    self.phase_boundaries[str(name)] = index

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "messages": self.messages,
            "entries": [entry.to_dict() for entry in self.entries],
            "current_entry_id": self.current_entry_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "cwd": self.cwd,
            "name": self.name,
            "phase_boundaries": self.phase_boundaries,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSession":
        raw_entries = data.get("entries", [])
        entries = [
            item if isinstance(item, SessionEntry) else SessionEntry.from_dict(item)
            for item in raw_entries
        ]
        return cls(
            session_id=data.get("session_id", ""),
            messages=data.get("messages", []),
            entries=entries,
            current_entry_id=data.get("current_entry_id"),
            schema_version=(
                data.get("schema_version", "agent_session.v2")
                if entries
                else "agent_session.v2"
            ),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            model=data.get("model"),
            stop_reason=data.get("stop_reason"),
            cwd=data.get("cwd"),
            name=data.get("name"),
            phase_boundaries=data.get("phase_boundaries", {}),
        )


def _message_key(message: Dict[str, Any]) -> tuple[Any, ...]:
    tool_calls = []
    for raw_call in message.get("tool_calls") or []:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            arguments = function.get("arguments", {})
        else:
            name = raw_call.get("name")
            arguments = raw_call.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, json.JSONDecodeError):
                pass
        tool_calls.append(
            (
                str(raw_call.get("id", "")),
                str(name or ""),
                json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str),
            )
        )
    return (
        str(message.get("role", "")),
        message.get("content"),
        str(message.get("tool_call_id", "")),
        tuple(tool_calls),
    )
