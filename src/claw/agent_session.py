"""Agent session management."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agent_types import AssistantTurn, ToolCall


@dataclass
class AgentSession:
    """Agent conversation session."""
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
    # Maps phase_name -> message_index where the phase started

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()
        if self.updated_at is None:
            self.updated_at = time.time()

    def add_user_message(self, content: str) -> None:
        """Add a user message to the session."""
        self.messages.append({"role": "user", "content": content})
        self.updated_at = time.time()

    def add_assistant_message(
        self,
        content: Optional[str] = None,
        tool_calls: Optional[List[ToolCall]] = None,
        thinking: Optional[str] = None,
        thinking_signature: Optional[str] = None,
    ) -> None:
        """Add an assistant message to the session.

        Args:
            content: Text content of the assistant reply.
            tool_calls: List of tool calls made.
            thinking: Raw thinking/reasoning content from models that support it
                (e.g. DeepSeek, Claude extended thinking). Stored for faithful
                replay in subsequent API calls.
            thinking_signature: Opaque signature associated with thinking block
                (required by some providers for verification).
        """
        msg: Dict[str, Any] = {"role": "assistant"}
        if content:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = [tc.to_dict() for tc in tool_calls]
        if thinking:
            msg["_thinking"] = thinking
        if thinking_signature:
            msg["_thinking_signature"] = thinking_signature
        self.messages.append(msg)
        self.updated_at = time.time()

    def add_tool_message(
        self,
        tool_call_id: str,
        content: str,
        tool_name: Optional[str] = None,
    ) -> None:
        """Add a tool result message to the session."""
        msg: Dict[str, Any] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
        if tool_name:
            msg["tool_name"] = tool_name
        self.messages.append(msg)
        self.updated_at = time.time()

    def add_system_message(self, content: str) -> None:
        """Add a system message to the session."""
        self.messages.append({"role": "system", "content": content})
        self.updated_at = time.time()

    def mark_phase_boundary(self, phase_name: str) -> None:
        """Insert a phase boundary marker at the current end of messages."""
        boundary_msg = {
            "role": "system",
            "content": f"[PHASE_BOUNDARY:{phase_name}]",
            "metadata": {"phase_boundary": True, "phase_name": phase_name},
        }
        self.messages.append(boundary_msg)
        self.phase_boundaries[phase_name] = len(self.messages) - 1
        self.updated_at = time.time()

    def get_phase_messages(self, phase_name: str) -> List[Dict[str, Any]]:
        """Get messages belonging to a specific phase (between its boundary and
        the next boundary or end of messages)."""
        start_idx = self.phase_boundaries.get(phase_name)
        if start_idx is None:
            return []
        boundary_indices = sorted(self.phase_boundaries.values())
        try:
            pos = boundary_indices.index(start_idx)
            if pos + 1 < len(boundary_indices):
                end_idx = boundary_indices[pos + 1]
            else:
                end_idx = len(self.messages)
        except ValueError:
            end_idx = len(self.messages)
        return self.messages[start_idx:end_idx]

    def get_messages(self) -> List[Dict[str, Any]]:
        """Get all messages."""
        return self.messages.copy()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize session to dict."""
        return {
            "session_id": self.session_id,
            "messages": self.messages,
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
    def from_dict(cls, data: Dict[str, Any]) -> AgentSession:
        """Create session from dict."""
        return cls(
            session_id=data.get("session_id", ""),
            messages=data.get("messages", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            model=data.get("model"),
            stop_reason=data.get("stop_reason"),
            cwd=data.get("cwd"),
            name=data.get("name"),
            phase_boundaries=data.get("phase_boundaries", {}),
        )