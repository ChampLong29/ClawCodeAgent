"""Context compression for CodeAgent.

Handles compaction of messages to control token usage.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

# Default buffer threshold for triggering compaction
AUTOCOMPACT_BUFFER_TOKENS = 150000

# Minimum messages to keep during compaction
MIN_MESSAGES_TO_KEEP = 4

# System message priority (always kept)
SYSTEM_MESSAGE_PRIORITY = 0


class CompactionStrategy(Enum):
    """Strategy for compaction."""
    SUMMARY = "summary"           # Summarize middle messages
    TRUNCATE = "truncate"         # Remove middle messages
    HYBRID = "hybrid"             # Summarize some, truncate others


def _estimate_token_count(text: str) -> int:
    """Estimate token count for text.

    This is a rough approximation - proper counting would use tiktoken or similar.
    For now, we use ~4 chars per token as a rough estimate.
    """
    if not text:
        return 0
    return len(text) // 4


def _get_message_priority(msg: Dict[str, Any]) -> int:
    """Determine priority of a message for compaction.

    Lower numbers = higher priority (kept longer).
    """
    role = msg.get("role", "")

    if role == "system":
        return SYSTEM_MESSAGE_PRIORITY

    if role == "user":
        return 10

    if role == "assistant":
        # Assistant messages with tool calls are higher priority
        if msg.get("tool_calls"):
            return 5
        return 20

    if role == "tool":
        return 15

    return 50


def compact_messages(
    messages: List[Dict[str, Any]],
    target_count: Optional[int] = None,
    strategy: CompactionStrategy = CompactionStrategy.HYBRID,
) -> List[Dict[str, Any]]:
    """Compact messages to reduce token usage.

    Args:
        messages: Original message list
        target_count: Target number of messages to keep (None = auto)
        strategy: Compaction strategy to use

    Returns:
        Compacted message list
    """
    if len(messages) <= MIN_MESSAGES_TO_KEEP:
        return messages

    # Keep system message
    system_msg = None
    if messages[0].get("role") == "system":
        system_msg = messages[0]

    # Keep last few messages (recent context is most valuable)
    keep_last = messages[-3:] if len(messages) > 4 else messages[-2:]

    # For hybrid/summary, summarize middle messages
    if strategy == CompactionStrategy.SUMMARY or strategy == CompactionStrategy.HYBRID:
        middle_start = 1
        middle_end = len(messages) - 3

        if middle_end > middle_start:
            middle_messages = messages[middle_start:middle_end]

            # Create summary of middle messages
            summary_content = _summarize_messages(middle_messages)

            summary_msg = {
                "role": "system",
                "content": f"[Earlier conversation summarized: {summary_content}]"
            }

            result = [system_msg] if system_msg else []
            result.append(summary_msg)
            result.extend(keep_last)
            return result

    # Truncate strategy - just keep system + first + last
    if strategy == CompactionStrategy.TRUNCATE:
        result = [system_msg] if system_msg else []
        result.append(messages[1])  # Keep first user message
        result.extend(keep_last)
        return result

    # Fallback: simple truncation
    if target_count and len(messages) > target_count:
        return messages[:target_count]

    return messages


def _summarize_messages(messages: List[Dict[str, Any]]) -> str:
    """Create a summary of multiple messages."""
    if not messages:
        return "empty"

    # Count by role
    roles = {}
    for msg in messages:
        role = msg.get("role", "unknown")
        roles[role] = roles.get(role, 0) + 1

    summary_parts = []
    for role, count in roles.items():
        summary_parts.append(f"{count} {role} message{'s' if count > 1 else ''}")

    # Include tool call info if present
    tool_calls = 0
    for msg in messages:
        if msg.get("tool_calls"):
            tool_calls += len(msg["tool_calls"])

    if tool_calls > 0:
        summary_parts.append(f"{tool_calls} tool call{'s' if tool_calls > 1 else ''}")

    return "; ".join(summary_parts)


def should_compact(messages: List[Dict[str, Any]], threshold: int = AUTOCOMPACT_BUFFER_TOKENS) -> bool:
    """Check if messages should be compacted based on estimated token count.

    Args:
        messages: Message list to check
        threshold: Token count threshold

    Returns:
        True if compaction should occur
    """
    total_chars = 0
    for msg in messages:
        if "content" in msg and msg["content"]:
            total_chars += len(msg["content"])
        if "tool_calls" in msg and msg["tool_calls"]:
            for tc in msg["tool_calls"]:
                if "function" in tc:
                    args = tc["function"].get("arguments", "")
                    total_chars += len(args)

    estimated_tokens = total_chars // 4
    return estimated_tokens > threshold


def compact(
    messages: List[Dict[str, Any]],
    reason: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Main compaction entry point.

    This is the function called by LocalCodingAgent when token count exceeds
    AUTOCOMPACT_BUFFER_TOKENS.

    Args:
        messages: Message list to compact
        reason: Optional reason for compaction (for logging)

    Returns:
        Compacted message list
    """
    return compact_messages(messages, strategy=CompactionStrategy.HYBRID)


def render_compaction_summary(original_count: int, compacted_count: int, reason: str) -> str:
    """Render a summary of compaction for logging/debugging."""
    removed = original_count - compacted_count
    return f"Compacted {removed} messages ({original_count} -> {compacted_count}). Reason: {reason}"


class CompactResult:
    """Result of a compaction operation."""
    def __init__(
        self,
        original_count: int,
        compacted_count: int,
        removed_messages: List[Dict[str, Any]],
        reason: str,
    ):
        self.original_count = original_count
        self.compacted_count = compacted_count
        self.removed_messages = removed_messages
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_count": self.original_count,
            "compacted_count": self.compacted_count,
            "removed": len(self.removed_messages),
            "reason": self.reason,
        }