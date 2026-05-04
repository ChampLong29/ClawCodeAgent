"""Agent manager runtime for CodeAgent."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class AgentInstance:
    """An agent instance."""
    id: str
    name: str
    status: str = "idle"  # idle, running, paused, stopped
    session_id: Optional[str] = None
    created_at: float = 0
    last_active: float = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }


class AgentManagerRuntime(RuntimeBase):
    """Agent instance lifecycle management runtime.

    Manages multiple agent instances and their states.
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.agents: Dict[str, AgentInstance] = {}
        self._load_state()

    def _get_state_path(self) -> str:
        """Get the state file path."""
        return os.path.join(self.cwd, ".port_sessions", "agent_manager.json")

    def _load_state(self) -> None:
        """Load state from file."""
        state_path = self._get_state_path()
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for a_data in data.get("agents", []):
                    agent = AgentInstance(
                        id=a_data["id"],
                        name=a_data["name"],
                        status=a_data.get("status", "idle"),
                        session_id=a_data.get("session_id"),
                        created_at=a_data.get("created_at", 0),
                        last_active=a_data.get("last_active", 0),
                    )
                    self.agents[agent.id] = agent
            except (json.JSONDecodeError, OSError):
                pass

    def _save_state(self) -> None:
        """Save state to file."""
        state_path = self._get_state_path()
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({
                "agents": [a.to_dict() for a in self.agents.values()],
            }, f, indent=2)

    def create_agent(self, name: str) -> str:
        """Create a new agent instance."""
        import time
        agent_id = str(uuid.uuid4())[:8]
        agent = AgentInstance(
            id=agent_id,
            name=name,
            created_at=time.time(),
            last_active=time.time(),
        )
        self.agents[agent_id] = agent
        self._save_state()
        return agent_id

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get an agent by ID."""
        if agent_id in self.agents:
            return self.agents[agent_id].to_dict()
        return None

    def list_agents(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all agents, optionally filtered."""
        agents = list(self.agents.values())
        if status:
            agents = [a for a in agents if a.status == status]
        return [a.to_dict() for a in agents]

    def update_agent(self, agent_id: str, status: Optional[str] = None, session_id: Optional[str] = None) -> bool:
        """Update an agent."""
        if agent_id not in self.agents:
            return False

        import time
        agent = self.agents[agent_id]
        if status:
            agent.status = status
        if session_id:
            agent.session_id = session_id
        agent.last_active = time.time()

        self._save_state()
        return True

    def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent."""
        if agent_id in self.agents:
            del self.agents[agent_id]
            self._save_state()
            return True
        return False

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "agents": [a.to_dict() for a in self.agents.values()],
            "count": len(self.agents),
            "running": len([a for a in self.agents.values() if a.status == "running"]),
        }

    def render_summary(self) -> str:
        """Render summary for context injection."""
        running = len([a for a in self.agents.values() if a.status == "running"])
        total = len(self.agents)

        if running > 0:
            return f"[Agent Manager] {running} running, {total} total"
        return f"[Agent Manager] {total} agent(s)"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        return ""

    def send_message(self, from_agent_id: str, to_agent_id: str, content: str) -> bool:
        """Send a message from one agent to another via shared message queue.

        Args:
            from_agent_id: ID of the sending agent
            to_agent_id: ID of the receiving agent
            content: Message content

        Returns:
            True if message was queued successfully
        """
        import time
        if from_agent_id not in self.agents or to_agent_id not in self.agents:
            return False

        message = {
            "from": from_agent_id,
            "to": to_agent_id,
            "content": content,
            "timestamp": time.time(),
        }

        # Store message in shared queue (persisted to state file)
        queue_path = os.path.join(self.cwd, ".port_sessions", "agent_messages.json")
        os.makedirs(os.path.dirname(queue_path), exist_ok=True)

        messages = []
        if os.path.exists(queue_path):
            try:
                with open(queue_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    messages = data.get("messages", [])
            except (json.JSONDecodeError, OSError):
                pass

        messages.append(message)

        with open(queue_path, "w", encoding="utf-8") as f:
            json.dump({"messages": messages}, f, indent=2)

        # Update last_active for sending agent
        if from_agent_id in self.agents:
            self.agents[from_agent_id].last_active = time.time()

        return True