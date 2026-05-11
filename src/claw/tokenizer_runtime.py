"""Tokenizer runtime for CodeAgent."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


class TokenizerRuntime(RuntimeBase):
    """Tokenizer and budget calculation runtime.

    Provides token counting and cost estimation.
    """

    # Approximate tokens per character (varies by model)
    TOKENS_PER_CHAR = 0.25

    def __init__(self, cwd: str):
        self.cwd = cwd

    def count_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        if not text:
            return 0
        return int(len(text) * self.TOKENS_PER_CHAR)

    def count_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count tokens in a message list."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self.count_tokens(content)

            # Add overhead for role
            total += 4

            # Add overhead for tool calls
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if "function" in tc:
                        args = tc["function"].get("arguments", "")
                        total += self.count_tokens(args)
                        total += 10  # overhead per tool call

        return total

    def estimate_cost(self, model_name: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost based on token counts."""
        # Import here to avoid circular dependency
        from .models import calculate_run_cost
        return calculate_run_cost(model_name, input_tokens, output_tokens)

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "tokens_per_char": self.TOKENS_PER_CHAR,
        }

    def render_summary(self) -> str:
        """Render summary for context injection."""
        return "[Tokenizer] Available for token counting"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        return ""