"""Context management helpers for CodeAgent."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ContextManager:
    """Manages context information for the agent."""

    def __init__(self):
        self._context: Dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        """Set a context value."""
        self._context[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a context value."""
        return self._context.get(key, default)

    def update(self, updates: Dict[str, Any]) -> None:
        """Update multiple context values."""
        self._context.update(updates)

    def clear(self) -> None:
        """Clear all context."""
        self._context = {}

    def to_dict(self) -> Dict[str, Any]:
        """Export context as dict."""
        return self._context.copy()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ContextManager:
        """Create from dict."""
        manager = cls()
        manager._context = data
        return manager


class ContextBuilder:
    """Builds context incrementally."""

    def __init__(self):
        self._sections: List[Dict[str, Any]] = []

    def add_section(self, name: str, content: str, priority: int = 0) -> ContextBuilder:
        """Add a section to the context."""
        self._sections.append({
            "name": name,
            "content": content,
            "priority": priority,
        })
        return self

    def build(self) -> str:
        """Build the final context string."""
        sorted_sections = sorted(self._sections, key=lambda s: s["priority"])
        return "\n\n".join(s["content"] for s in sorted_sections if s["content"])


def merge_contexts(*contexts: Dict[str, Any]) -> Dict[str, Any]:
    """Merge multiple context dicts."""
    result = {}
    for ctx in contexts:
        result.update(ctx)
    return result


def filter_context(context: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """Filter context to only include specified keys."""
    return {k: v for k, v in context.items() if k in keys}