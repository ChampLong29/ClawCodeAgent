"""LSP runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class LSPServerConfig:
    """LSP server configuration."""
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    root_uri: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "root_uri": self.root_uri,
        }


class LSPRuntime(RuntimeBase):
    """Language Server Protocol runtime.

    Discovery paths:
    - .claw-lsp.json
    - .lsp.json
    """

    CONFIG_FILES = [".claw-lsp.json", ".lsp.json"]

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.servers = self._discover()

    def _discover(self) -> List[LSPServerConfig]:
        """Discover LSP server configuration."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(self.cwd, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_servers(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return []

    def _parse_servers(self, data: Dict[str, Any]) -> List[LSPServerConfig]:
        """Parse LSP server configurations."""
        servers = []
        for s_data in data.get("servers", []):
            servers.append(LSPServerConfig(
                name=s_data.get("name", ""),
                command=s_data.get("command", ""),
                args=s_data.get("args", []),
                env=s_data.get("env", {}),
                root_uri=s_data.get("rootUri"),
            ))
        return servers

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "servers": [s.to_dict() for s in self.servers],
            "count": len(self.servers),
        }

    def list_servers(self) -> List[Dict[str, Any]]:
        """List all LSP servers."""
        return [s.to_dict() for s in self.servers]

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.servers:
            return "No LSP servers configured."

        names = [s.name for s in self.servers]
        return f"[LSP Servers] {', '.join(names)}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.servers:
            return ""

        return "LSP servers are available for code intelligence."