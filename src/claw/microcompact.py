"""Micro-compaction utilities for CodeAgent.

Lightweight compaction functions for specific scenarios.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def micro_compact_text(text: str, max_length: int = 1000) -> str:
    """Compact text to maximum length."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def micro_compact_messages(messages: List[Dict[str, Any]], max_messages: int = 10) -> List[Dict[str, Any]]:
    """Micro-compact messages to maximum count."""
    if len(messages) <= max_messages:
        return messages

    # Always keep system message
    if messages and messages[0].get("role") == "system":
        result = [messages[0]]
        result.extend(messages[-(max_messages - 1):])
        return result

    return messages[-max_messages:]


def truncate_tool_result(result: Any, max_length: int = 2000) -> str:
    """Truncate tool result to maximum length."""
    result_str = str(result)
    if len(result_str) <= max_length:
        return result_str
    return result_str[:max_length] + f"... [truncated {len(result_str) - max_length} chars]"