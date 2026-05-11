"""Team runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class TeamMember:
    """A team member."""
    name: str
    role: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
        }


@dataclass
class Team:
    """A team definition."""
    name: str
    description: str = ""
    members: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "members": self.members,
        }


class TeamRuntime(RuntimeBase):
    """Team collaboration runtime.

    Discovery paths:
    - .claw-teams.json
    - .claw-team.json
    - .claude/teams.json  (SPECIAL: additional location)
    """

    CONFIG_FILES = [".claw-teams.json", ".claw-team.json"]
    ADDITIONAL_FILES = [".claude/teams.json"]

    def __init__(self, cwd: str, additional_directories: Optional[List[str]] = None):
        self.cwd = cwd
        self.additional_directories = additional_directories or []
        self.teams = self._discover()

    def _discover(self) -> List[Team]:
        """Discover team configurations."""
        # Check standard locations
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(self.cwd, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_teams(data)
                except (json.JSONDecodeError, OSError):
                    continue

        # Check additional locations
        for filename in self.ADDITIONAL_FILES:
            filepath = os.path.join(self.cwd, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_teams(data)
                except (json.JSONDecodeError, OSError):
                    continue

        return []

    def _parse_teams(self, data: Dict[str, Any]) -> List[Team]:
        """Parse teams from configuration."""
        teams = []
        for t_data in data.get("teams", []):
            teams.append(Team(
                name=t_data.get("name", ""),
                description=t_data.get("description", ""),
                members=t_data.get("members", []),
            ))
        return teams

    def get_team(self, name: str) -> Optional[Team]:
        """Get a team by name."""
        for team in self.teams:
            if team.name == name:
                return team
        return None

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "teams": [t.to_dict() for t in self.teams],
            "count": len(self.teams),
        }

    def list_teams(self) -> List[Dict[str, Any]]:
        """List all teams."""
        return [t.to_dict() for t in self.teams]

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.teams:
            return "No teams configured."

        names = [t.name for t in self.teams]
        return f"[Teams] {', '.join(names)}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.teams:
            return ""

        total_members = sum(len(t.members) for t in self.teams)
        return f"{len(self.teams)} team(s) with {total_members} total member(s)."