"""Type definitions for CodeAgent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BudgetExceededAction(Enum):
    """Action when budget is exceeded."""
    STOP = "stop"
    COMPACT = "compact"
    WARN = "warn"


@dataclass
class ModelPricing:
    """Model pricing for cost calculation."""
    input_token_price: float = 0.0
    output_token_price: float = 0.0

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate total cost based on token usage."""
        return (input_tokens * self.input_token_price) + (output_tokens * self.output_token_price)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_token_price": self.input_token_price,
            "output_token_price": self.output_token_price,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelPricing:
        return cls(
            input_token_price=data.get("input_token_price", 0.0),
            output_token_price=data.get("output_token_price", 0.0),
        )


@dataclass
class ModelConfig:
    """Model configuration."""
    name: str = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
    temperature: float = 0.1
    max_tokens: Optional[int] = None
    pricing: Optional[ModelPricing] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "pricing": self.pricing.to_dict() if self.pricing else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelConfig:
        pricing = None
        if data.get("pricing"):
            pricing = ModelPricing.from_dict(data["pricing"])
        return cls(
            name=data.get("name", "Qwen/Qwen3-Coder-30B-A3B-Instruct"),
            temperature=data.get("temperature", 0.1),
            max_tokens=data.get("max_tokens"),
            pricing=pricing,
        )


@dataclass
class AgentRuntimeConfig:
    """Agent runtime configuration."""
    model: ModelConfig = field(default_factory=ModelConfig)
    max_turns: int = 100
    timeout_seconds: int = 300

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentRuntimeConfig:
        model = ModelConfig.from_dict(data.get("model", {}))
        return cls(
            model=model,
            max_turns=data.get("max_turns", 100),
            timeout_seconds=data.get("timeout_seconds", 300),
        )


@dataclass
class AgentPermissions:
    """Agent permissions configuration."""
    allow_write: bool = False
    allow_shell: bool = False
    allowed_tools: Optional[List[str]] = None
    denied_tools: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allow_write": self.allow_write,
            "allow_shell": self.allow_shell,
            "allowed_tools": self.allowed_tools,
            "denied_tools": self.denied_tools,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentPermissions:
        return cls(
            allow_write=data.get("allow_write", False),
            allow_shell=data.get("allow_shell", False),
            allowed_tools=data.get("allowed_tools"),
            denied_tools=data.get("denied_tools"),
        )


@dataclass
class BudgetConfig:
    """Budget configuration for agent."""
    max_total_tokens: int = 250000
    max_output_tokens: int = 120000
    max_tool_calls: int = 500
    max_model_calls: int = 120

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_total_tokens": self.max_total_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_tool_calls": self.max_tool_calls,
            "max_model_calls": self.max_model_calls,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BudgetConfig:
        return cls(
            max_total_tokens=data.get("max_total_tokens", 250000),
            max_output_tokens=data.get("max_output_tokens", 120000),
            max_tool_calls=data.get("max_tool_calls", 500),
            max_model_calls=data.get("max_model_calls", 120),
        )


@dataclass
class OutputSchemaConfig:
    """Output schema configuration."""
    format: str = "text"  # text, json, markdown
    include_metadata: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": self.format,
            "include_metadata": self.include_metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OutputSchemaConfig:
        return cls(
            format=data.get("format", "text"),
            include_metadata=data.get("include_metadata", True),
        )


@dataclass
class ToolCall:
    """A tool call from the model."""
    id: str
    name: str
    arguments: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolCall:
        return cls(
            id=data["id"],
            name=data["name"],
            arguments=data.get("arguments", {}),
        )


@dataclass
class AssistantTurn:
    """An assistant turn in the conversation."""
    role: str = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    function_call: Optional[Dict[str, Any]] = None  # Legacy support

    def to_dict(self) -> Dict[str, Any]:
        result = {"role": self.role}
        if self.content is not None:
            result["content"] = self.content
        if self.tool_calls:
            result["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.function_call:
            result["function_call"] = self.function_call
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AssistantTurn:
        tool_calls = None
        if data.get("tool_calls"):
            tool_calls = [ToolCall.from_dict(tc) for tc in data["tool_calls"]]
        return cls(
            role=data.get("role", "assistant"),
            content=data.get("content"),
            tool_calls=tool_calls,
            function_call=data.get("function_call"),
        )


@dataclass
class UsageStats:
    """Usage statistics for a run."""
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0

    def __iadd__(self, other: UsageStats) -> UsageStats:
        """Add other UsageStats to this one."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.model_calls += other.model_calls
        self.tool_calls += other.tool_calls
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UsageStats:
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            model_calls=data.get("model_calls", 0),
            tool_calls=data.get("tool_calls", 0),
        )


@dataclass
class AgentRunResult:
    """Result of an agent run."""
    stop_reason: str  # completed, budget_exceeded, error, stopped
    final_message: Optional[str] = None
    usage: Optional[UsageStats] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stop_reason": self.stop_reason,
            "final_message": self.final_message,
            "usage": self.usage.to_dict() if self.usage else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentRunResult:
        usage = None
        if data.get("usage"):
            usage = UsageStats.from_dict(data["usage"])
        return cls(
            stop_reason=data.get("stop_reason", "completed"),
            final_message=data.get("final_message"),
            usage=usage,
            error=data.get("error"),
        )