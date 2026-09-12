"""Plugin runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .hook_policy import RuntimeBase
from .runtime_events import EventDirective, RuntimeEvent, RuntimeEventBus


@dataclass
class PluginConfig:
    """Configuration for a plugin."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    tool_aliases: List[Dict[str, Any]] = field(default_factory=list)
    virtual_tools: List[Dict[str, Any]] = field(default_factory=list)
    blocked_tools: List[str] = field(default_factory=list)
    hooks: Dict[str, Any] = field(default_factory=dict)
    tool_hooks: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "tool_aliases": self.tool_aliases,
            "virtual_tools": self.virtual_tools,
            "blocked_tools": self.blocked_tools,
            "hooks": self.hooks,
            "tool_hooks": self.tool_hooks,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PluginConfig:
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            tool_aliases=data.get("tool_aliases", []),
            virtual_tools=data.get("virtual_tools", []),
            blocked_tools=data.get("blocked_tools", []),
            hooks=data.get("hooks", {}),
            tool_hooks=data.get("tool_hooks", {}),
        )


class PluginRuntime(RuntimeBase):
    """Plugin runtime for discovering and managing plugins.

    Discovery paths:
    - .codex-plugin/plugin.json
    - .claw-plugin/plugin.json
    - plugins/*/plugin.json
    """

    CONFIG_FILES = ["plugin.json"]

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.plugins = self._discover()

    def _discover(self) -> List[PluginConfig]:
        """Discover plugins in the workspace."""
        plugins = []

        # Check plugin directories
        search_paths = [
            os.path.join(self.cwd, ".codex-plugin"),
            os.path.join(self.cwd, ".claw-plugin"),
            os.path.join(self.cwd, "plugins"),
        ]

        for search_path in search_paths:
            if not os.path.exists(search_path):
                continue

            if os.path.isfile(search_path):
                # Single plugin.json file
                if search_path.endswith("plugin.json"):
                    config = self._load_plugin(search_path)
                    if config:
                        plugins.append(config)
            else:
                # Directory - check for plugin.json directly or in subdirectories
                # Support: .claw-plugin/plugin.json (single plugin)
                direct_json = os.path.join(search_path, "plugin.json")
                if os.path.exists(direct_json):
                    config = self._load_plugin(direct_json)
                    if config:
                        plugins.append(config)
                # Support: .claw-plugin/<name>/plugin.json (multiple plugins)
                for entry in os.listdir(search_path):
                    plugin_dir = os.path.join(search_path, entry)
                    if os.path.isdir(plugin_dir):
                        plugin_json = os.path.join(plugin_dir, "plugin.json")
                        if os.path.exists(plugin_json):
                            config = self._load_plugin(plugin_json)
                            if config:
                                if not config.name:
                                    config.name = entry  # Use directory name if not in config
                                plugins.append(config)

        return plugins

    def _load_plugin(self, filepath: str) -> Optional[PluginConfig]:
        """Load a plugin configuration."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return PluginConfig.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all discovered plugins."""
        return [p.to_dict() for p in self.plugins]

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "plugins": self.list_plugins(),
            "count": len(self.plugins),
        }

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.plugins:
            return "No plugins discovered."

        names = [p.name for p in self.plugins]
        return f"[Plugins] {', '.join(names)}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.plugins:
            return ""

        has_hooks = any(p.hooks for p in self.plugins)
        if has_hooks:
            return "Custom hooks are configured by plugins."

        return ""

    def bind_event_bus(self, event_bus: RuntimeEventBus) -> List[str]:
        """Register safe declarative plugin hooks on ``event_bus``.

        This adapter deliberately does not import or execute workspace Python.
        Supported hook fields are ``cancel``/``deny``, ``reason``, static
        ``payload_updates``, and ``append_system_prompt``.  Tool hooks may also
        use ``argument_equals`` to restrict a rule to matching arguments.
        """
        tokens: List[str] = []
        for plugin in self.plugins:
            for event_type, raw_spec in plugin.hooks.items():
                for spec in self._normalize_hook_specs(raw_spec):
                    tokens.append(
                        event_bus.on(
                            str(event_type),
                            self._make_hook_listener(plugin.name, spec),
                            priority=int(spec.get("priority", 0)),
                            name=f"plugin:{plugin.name}:{event_type}",
                            fail_closed=bool(spec.get("fail_closed", False)),
                        )
                    )

            for tool_name, raw_tool_spec in plugin.tool_hooks.items():
                if not isinstance(raw_tool_spec, Mapping):
                    continue
                before = raw_tool_spec.get("before", raw_tool_spec)
                after = raw_tool_spec.get("after")
                for event_type, raw_spec in (
                    ("before_tool_call", before),
                    ("tool_result", after),
                ):
                    for spec in self._normalize_hook_specs(raw_spec):
                        hook_spec = dict(spec)
                        hook_spec["tool"] = str(tool_name)
                        tokens.append(
                            event_bus.on(
                                event_type,
                                self._make_hook_listener(plugin.name, hook_spec),
                                priority=int(hook_spec.get("priority", 0)),
                                name=f"plugin:{plugin.name}:{event_type}:{tool_name}",
                                fail_closed=bool(hook_spec.get("fail_closed", False)),
                            )
                        )
        return tokens

    @staticmethod
    def _normalize_hook_specs(raw_spec: Any) -> List[Dict[str, Any]]:
        if raw_spec is None:
            return []
        if isinstance(raw_spec, Mapping):
            return [dict(raw_spec)]
        if isinstance(raw_spec, list):
            return [dict(item) for item in raw_spec if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _make_hook_listener(plugin_name: str, spec: Dict[str, Any]):
        def listener(event: RuntimeEvent) -> Optional[EventDirective]:
            required_tool = str(spec.get("tool", ""))
            observed_tool = str(
                event.payload.get("actual_tool_name")
                or event.payload.get("tool_name")
                or ""
            )
            if required_tool and observed_tool != required_tool:
                return None

            required_args = spec.get("argument_equals", {})
            arguments = event.payload.get("arguments", {})
            if isinstance(required_args, Mapping):
                if not isinstance(arguments, Mapping):
                    return None
                if any(arguments.get(key) != value for key, value in required_args.items()):
                    return None

            updates = spec.get("payload_updates", {})
            payload_updates = dict(updates) if isinstance(updates, Mapping) else {}
            append_prompt = spec.get("append_system_prompt")
            if isinstance(append_prompt, str) and append_prompt.strip():
                current = str(event.payload.get("system_prompt", ""))
                separator = "\n\n" if current else ""
                payload_updates["system_prompt"] = (
                    current + separator + append_prompt.strip()
                )

            cancelled = bool(spec.get("cancel", spec.get("deny", False)))
            reason = str(
                spec.get("reason")
                or (f"blocked by plugin {plugin_name}" if cancelled else "")
            )
            if not payload_updates and not cancelled:
                return None
            return EventDirective(
                cancel=cancelled,
                reason=reason,
                payload_updates=payload_updates,
            )

        return listener
