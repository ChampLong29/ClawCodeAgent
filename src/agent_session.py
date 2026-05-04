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
    ) -> None:
        """Add an assistant message to the session."""
        msg: Dict[str, Any] = {"role": "assistant"}
        if content:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = [tc.to_dict() for tc in tool_calls]
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
        )