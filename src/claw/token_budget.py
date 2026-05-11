"""Token budget tracking and preflight checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .agent_types import BudgetConfig, UsageStats


@dataclass
class TokenBudget:
    """Token budget tracker."""
    config: BudgetConfig
    usage: UsageStats

    @classmethod
    def create(cls, config: Optional[BudgetConfig] = None) -> TokenBudget:
        """Create a new budget tracker."""
        if config is None:
            config = BudgetConfig()
        return cls(config=config, usage=UsageStats())

    def check(self) -> tuple[bool, Optional[str]]:
        """Check if budget allows continued execution.

        Returns (allowed, reason_if_not).
        """
        total_tokens = self.usage.input_tokens + self.usage.output_tokens

        if total_tokens >= self.config.max_total_tokens:
            return False, f"max_total_tokens exceeded: {total_tokens} >= {self.config.max_total_tokens}"

        if self.usage.output_tokens >= self.config.max_output_tokens:
            return False, f"max_output_tokens exceeded: {self.usage.output_tokens} >= {self.config.max_output_tokens}"

        if self.usage.tool_calls >= self.config.max_tool_calls:
            return False, f"max_tool_calls exceeded: {self.usage.tool_calls} >= {self.config.max_tool_calls}"

        if self.usage.model_calls >= self.config.max_model_calls:
            return False, f"max_model_calls exceeded: {self.usage.model_calls} >= {self.config.max_model_calls}"

        return True, None

    def update_usage(self, usage: UsageStats) -> None:
        """Update usage stats."""
        self.usage += usage

    def remaining_tokens(self) -> int:
        """Get remaining token budget."""
        total_tokens = self.usage.input_tokens + self.usage.output_tokens
        return max(0, self.config.max_total_tokens - total_tokens)

    def to_dict(self):
        return {
            "config": self.config.to_dict(),
            "usage": self.usage.to_dict(),
            "remaining": self.remaining_tokens(),
        }