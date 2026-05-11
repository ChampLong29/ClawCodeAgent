"""Session memory compaction utilities."""

from __future__ import annotations

from typing import Any, Dict, List


def compact_session_memory(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact session memory by removing redundant information.

    This is a lighter-weight compaction than the main compact.py
    and is used for in-memory operations.
    """
    if len(messages) <= 6:
        return messages

    # Keep first (system) and last 4 messages
    result = [messages[0]]
    result.extend(messages[-4:])
    return result


def extract_memory_key_facts(messages: List[Dict[str, Any]]) -> List[str]:
    """Extract key facts from messages for memory."""
    facts = []

    for msg in messages:
        content = msg.get("content", "")
        if not content:
            continue

        # Extract tool results
        if msg.get("role") == "tool":
            tool_name = msg.get("tool_name", "unknown")
            facts.append(f"Tool {tool_name} executed successfully")

        # Extract user requests
        if msg.get("role") == "user":
            # First 100 chars of user messages
            if len(content) > 100:
                facts.append(f"User request: {content[:100]}...")
            else:
                facts.append(f"User request: {content}")

    return facts[:20]  # Limit to 20 facts