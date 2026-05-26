"""Transcript handling for CodeAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json


@dataclass
class TranscriptEntry:
    """A single entry in the transcript."""
    timestamp: float
    role: str  # user, assistant, tool, system
    content: str
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "role": self.role,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TranscriptEntry:
        return cls(
            timestamp=data.get("timestamp", 0),
            role=data.get("role", ""),
            content=data.get("content", ""),
            metadata=data.get("metadata"),
        )


@dataclass
class Transcript:
    """Transcript of agent conversation."""
    session_id: str
    entries: List[TranscriptEntry] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_entry(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add an entry to the transcript."""
        import time
        self.entries.append(TranscriptEntry(
            timestamp=time.time(),
            role=role,
            content=content,
            metadata=metadata,
        ))

    def add_user(self, content: str) -> None:
        """Add a user entry."""
        self.add_entry("user", content)

    def add_assistant(self, content: str) -> None:
        """Add an assistant entry."""
        self.add_entry("assistant", content)

    def add_tool(self, content: str, tool_name: Optional[str] = None) -> None:
        """Add a tool entry."""
        self.add_entry("tool", content, {"tool_name": tool_name})

    def add_system(self, content: str) -> None:
        """Add a system entry."""
        self.add_entry("system", content)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "entries": [e.to_dict() for e in self.entries],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Transcript:
        entries = [TranscriptEntry.from_dict(e) for e in data.get("entries", [])]
        return cls(
            session_id=data.get("session_id", ""),
            entries=entries,
            metadata=data.get("metadata", {}),
        )

    def to_json(self) -> str:
        """Export transcript as JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> Transcript:
        """Import transcript from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def get_text(self) -> str:
        """Get full transcript as plain text."""
        lines = []
        for entry in self.entries:
            role_label = entry.role.upper()
            lines.append(f"[{role_label}] {entry.content}")
        return "\n".join(lines)