"""Slash command logic for CodeAgent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class SlashCommand:
    """A slash command definition."""
    name: str
    description: str
    handler: Callable[..., Any]
    parameters: Optional[Dict[str, Any]] = None


class SlashCommandRegistry:
    """Registry for slash commands."""

    def __init__(self):
        self.commands: Dict[str, SlashCommand] = {}

    def register(self, command: SlashCommand) -> None:
        self.commands[command.name] = command

    def get(self, name: str) -> Optional[SlashCommand]:
        return self.commands.get(name)

    def list_commands(self) -> List[SlashCommand]:
        return list(self.commands.values())

    def parse_input(self, input_str: str) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Parse slash command input.

        Returns (command_name, arguments) tuple.
        """
        if not input_str.startswith("/"):
            return None, None

        parts = input_str[1:].split(None, 1)
        command_name = parts[0]
        args_str = parts[1] if len(parts) > 1 else ""

        # Parse arguments (simple key=value pairs)
        args = {}
        if args_str:
            for part in args_str.split():
                if "=" in part:
                    key, value = part.split("=", 1)
                    args[key] = value
                else:
                    args["_"] = part

        return command_name, args


# Global registry
_global_registry: Optional[SlashCommandRegistry] = None


def default_command_registry() -> SlashCommandRegistry:
    """Get or create the default slash command registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = _build_default_registry()
    return _global_registry


def _build_default_registry() -> SlashCommandRegistry:
    """Build the default slash command registry."""
    registry = SlashCommandRegistry()

    # /help command
    registry.register(SlashCommand(
        name="help",
        description="Show available commands",
        handler=_handle_help,
    ))

    # /retry command
    registry.register(SlashCommand(
        name="retry",
        description="Retry the last assistant message",
        handler=_handle_retry,
    ))

    # /compact command
    registry.register(SlashCommand(
        name="compact",
        description="Compact the conversation to save tokens",
        handler=_handle_compact,
    ))

    # /budget command
    registry.register(SlashCommand(
        name="budget",
        description="Show current budget usage",
        handler=_handle_budget,
    ))

    return registry


def _handle_help(args: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Handle /help command."""
    registry = default_command_registry()
    lines = ["Available commands:"]
    for cmd in registry.list_commands():
        lines.append(f"  /{cmd.name} - {cmd.description}")
    return "\n".join(lines)


def _handle_retry(args: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Handle /retry command."""
    return "RETRY_REQUESTED"


def _handle_compact(args: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Handle /compact command."""
    return "COMPACT_REQUESTED"


def _handle_budget(args: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Handle /budget command."""
    budget = context.get("budget")
    if budget:
        remaining = budget.remaining_tokens()
        return f"Remaining tokens: {remaining}"
    return "No budget information available"


def execute_slash_command(
    input_str: str,
    context: Optional[Dict[str, Any]] = None,
) -> Any:
    """Execute a slash command from input string."""
    registry = default_command_registry()
    command_name, args = registry.parse_input(input_str)

    if not command_name:
        return None

    command = registry.get(command_name)
    if not command:
        return {"error": f"Unknown command: /{command_name}"}

    return command.handler(args or {}, context or {})