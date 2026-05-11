"""Bridge runtime — external platform integration via routing gateways.

Supports Feishu, WeCom, and extensible bridge types. Each bridge maps
external platform channels to named agent sessions using hierarchical
routing paths (e.g., "feishu/user/ou_abc123").
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .hook_policy import RuntimeBase

if TYPE_CHECKING:
    from .agent_runtime import LocalCodingAgent


@dataclass
class BridgeConfig:
    """Configuration for a single bridge."""
    type: str = ""            # feishu, wecom, webhook, slack, telegram
    name: str = ""            # unique name for this bridge instance
    app_id: str = ""
    app_secret: str = ""
    webhook_url: str = ""     # ingress endpoint path (e.g., "/api/bridge/feishu_main/webhook")
    verify_token: str = ""
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "app_id": self.app_id,
            "app_secret": "***" if self.app_secret else "",
            "webhook_url": self.webhook_url,
            "verify_token": "***" if self.verify_token else "",
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BridgeConfig:
        return cls(
            type=data.get("type", ""),
            name=data.get("name", ""),
            app_id=data.get("app_id", ""),
            app_secret=data.get("app_secret", ""),
            webhook_url=data.get("webhook_url", ""),
            verify_token=data.get("verify_token", ""),
            enabled=data.get("enabled", True),
        )

    @property
    def credentials(self) -> Dict[str, str]:
        """Get credentials dict for API calls."""
        return {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
            "verify_token": self.verify_token,
        }


class BridgeRuntime(RuntimeBase):
    """Runtime for external platform bridge integration.

    Configuration discovery: .claw-bridge.json in cwd (non-walk-up).

    Each bridge maps (user_id, chat_id) → session_id. Sessions are
    named using the routing path: {bridge_type}/{user_or_group}/{external_id}
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.bridges: Dict[str, BridgeConfig] = {}
        self._routing_table: Dict[str, Dict[str, str]] = {}
        self._routing_file = os.path.join(cwd, ".port_sessions", "bridge_routing.json")

        self._discover_configs()
        self._load_routing_table()

    # ------------------------------------------------------------------
    # Config discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _discover_config_files(cwd: str) -> List[str]:
        """Find bridge config files (non-walk-up, only cwd)."""
        paths = []
        for name in (".claw-bridge.json",):
            full = os.path.join(cwd, name)
            if os.path.isfile(full):
                paths.append(full)
        return paths

    def _discover_configs(self) -> None:
        """Discover and load bridge configurations."""
        for path in self._discover_config_files(self.cwd):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for bd in data.get("bridges", []):
                    cfg = BridgeConfig.from_dict(bd)
                    if cfg.name:
                        self.bridges[cfg.name] = cfg
            except (json.JSONDecodeError, OSError):
                pass

    # ------------------------------------------------------------------
    # Routing table
    # ------------------------------------------------------------------

    def _load_routing_table(self) -> None:
        """Load the routing table from disk."""
        if os.path.exists(self._routing_file):
            try:
                with open(self._routing_file, "r", encoding="utf-8") as f:
                    self._routing_table = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._routing_table = {}

    def _save_routing_table(self) -> None:
        """Persist the routing table to disk."""
        os.makedirs(os.path.dirname(self._routing_file), exist_ok=True)
        with open(self._routing_file, "w", encoding="utf-8") as f:
            json.dump(self._routing_table, f, indent=2)

    def _routing_key(self, user_id: str, chat_id: str) -> str:
        """Build a routing key from user and chat identifiers."""
        return f"{user_id}:{chat_id}"

    def resolve_session(
        self,
        bridge_name: str,
        user_id: str,
        chat_id: str,
    ) -> str:
        """Resolve or create a session ID for the given routing path.

        If no session exists for this (user, chat), a new session
        is created in the session store with a name derived from the
        routing path.

        Returns the session_id.
        """
        from .session_store import load_session_by_name
        from .agent_session import AgentSession
        import uuid

        if bridge_name not in self._routing_table:
            self._routing_table[bridge_name] = {}

        bridge = self.bridges.get(bridge_name)
        bridge_type = bridge.type if bridge else "unknown"

        key = self._routing_key(user_id, chat_id)
        existing_session_id = self._routing_table[bridge_name].get(key)

        if existing_session_id:
            # Verify the session still exists
            try:
                from .session_store import load_agent_session
                load_agent_session(existing_session_id, self.cwd)
                return existing_session_id
            except FileNotFoundError:
                pass  # Session deleted, create new one

        # Create a new session with a routing-derived name
        session_name = f"{bridge_type}/{user_id}/{chat_id}"
        session_id = str(uuid.uuid4())[:8]

        session = AgentSession(session_id=session_id, name=session_name, cwd=self.cwd)
        from .session_store import save_agent_session
        save_agent_session(session, self.cwd)

        # Update routing table
        self._routing_table[bridge_name][key] = session_id
        self._save_routing_table()

        return session_id

    def route_message(
        self,
        bridge_name: str,
        user_id: str,
        chat_id: str,
        content: str,
    ) -> Dict[str, Any]:
        """Route an incoming message to the appropriate agent session.

        Returns {"session_id": str, "response": str, "error": str | None}
        """
        try:
            session_id = self.resolve_session(bridge_name, user_id, chat_id)

            from .agent_runtime import LocalCodingAgent
            agent = LocalCodingAgent.from_session(
                session_id=session_id,
                cwd=self.cwd,
            )

            result = agent.resume(prompt=content, stream=False)

            # Persist
            from .session_store import save_agent_session
            if agent.session:
                save_agent_session(agent.session, self.cwd)

            return {
                "session_id": session_id,
                "response": result.final_message or "",
                "error": result.error,
                "stop_reason": result.stop_reason,
            }
        except Exception as e:
            return {
                "session_id": "",
                "response": "",
                "error": str(e),
            }

    def get_bridge_sessions(self, bridge_name: str) -> List[Dict[str, Any]]:
        """Get all sessions associated with a bridge."""
        if bridge_name not in self.bridges:
            return []

        bridge = self.bridges[bridge_name]
        prefix = f"{bridge.type}/"

        from .session_store import list_sessions_by_prefix
        return list_sessions_by_prefix(prefix, self.cwd)

    def get_routing_table(self) -> Dict[str, Any]:
        """Get the full routing table for debugging."""
        result = {}
        for bridge_name, routes in self._routing_table.items():
            bridge = self.bridges.get(bridge_name)
            bridge_type = bridge.type if bridge else "unknown"
            result[bridge_name] = {
                "type": bridge_type,
                "routes": routes,
            }
        return result

    # ------------------------------------------------------------------
    # RuntimeBase interface
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        """Get current bridge state."""
        return {
            "bridges": {name: cfg.to_dict() for name, cfg in self.bridges.items()},
            "routing_table": self.get_routing_table(),
            "session_count": sum(
                len(routes) for routes in self._routing_table.values()
            ),
        }

    def render_summary(self) -> str:
        """Render one-line summary."""
        active = [name for name, cfg in self.bridges.items() if cfg.enabled]
        if not active:
            return ""
        return f"[Bridge] {len(active)} bridge(s): {', '.join(active)}"

    def get_prompt_guidance(self) -> str:
        """No system prompt injection for bridges."""
        return ""
