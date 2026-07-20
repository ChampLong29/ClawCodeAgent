"""Claw Code Agent - A Claude Code style agent runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "1.0.0"

_LAZY_EXPORTS = {
    "AgentRunResult": (".agent_types", "AgentRunResult"),
    "BudgetConfig": (".agent_types", "BudgetConfig"),
    "ModelConfig": (".agent_types", "ModelConfig"),
    "ToolCall": (".agent_types", "ToolCall"),
    "UsageStats": (".agent_types", "UsageStats"),
    "AgentSession": (".agent_session", "AgentSession"),
    "ToolExecutionResult": (".agent_tools", "ToolExecutionResult"),
    "SecurityResult": (".bash_security", "SecurityResult"),
    "LocalCodingAgent": (".agent_runtime", "LocalCodingAgent"),
    "QueryEngine": (".query_engine", "QueryEngine"),
}

__all__ = [
    "AgentRunResult",
    "AgentSession",
    "BudgetConfig",
    "LocalCodingAgent",
    "ModelConfig",
    "QueryEngine",
    "SecurityResult",
    "ToolCall",
    "ToolExecutionResult",
    "UsageStats",
]


def __getattr__(name: str) -> Any:
    """Load public API objects only when requested."""
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value


if TYPE_CHECKING:
    from .agent_runtime import LocalCodingAgent
    from .agent_session import AgentSession
    from .agent_tools import ToolExecutionResult
    from .agent_types import AgentRunResult, BudgetConfig, ModelConfig, ToolCall, UsageStats
    from .bash_security import SecurityResult
    from .query_engine import QueryEngine
