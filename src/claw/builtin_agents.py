"""Built-in agent type definitions for CodeAgent."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentType(Enum):
    """Type of agent."""
    BASE = "base"
    CODING = "coding"
    REVIEW = "review"
    SEARCH = "search"
    TASK = "task"


@dataclass
class BuiltInAgent:
    """Definition of a built-in agent."""
    name: str
    type: AgentType
    description: str
    capabilities: List[str]
    system_prompt_template: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.value,
            "description": self.description,
            "capabilities": self.capabilities,
        }


# Built-in agents registry
BUILT_IN_AGENTS = {
    AgentType.BASE: BuiltInAgent(
        name="base",
        type=AgentType.BASE,
        description="Basic agent for general tasks",
        capabilities=["general", "chat"],
        system_prompt_template="You are a helpful assistant.",
    ),
    AgentType.CODING: BuiltInAgent(
        name="coding",
        type=AgentType.CODING,
        description="Agent specialized in coding tasks",
        capabilities=["read_file", "write_file", "edit_file", "bash", "glob_search", "grep_search"],
        system_prompt_template="You are an expert coding assistant. Help with programming tasks.",
    ),
    AgentType.REVIEW: BuiltInAgent(
        name="review",
        type=AgentType.REVIEW,
        description="Agent for code review",
        capabilities=["read_file", "grep_search"],
        system_prompt_template="You are a code review expert. Provide thorough and constructive feedback.",
    ),
    AgentType.SEARCH: BuiltInAgent(
        name="search",
        type=AgentType.SEARCH,
        description="Agent for searching and researching",
        capabilities=["grep_search", "glob_search", "read_file"],
        system_prompt_template="You are a research assistant. Help find and summarize information.",
    ),
    AgentType.TASK: BuiltInAgent(
        name="task",
        type=AgentType.TASK,
        description="Agent for task execution",
        capabilities=["read_file", "write_file", "bash"],
        system_prompt_template="You are a task execution agent. Complete tasks accurately and efficiently.",
    ),
}


def get_builtin_agent(agent_type: AgentType) -> Optional[BuiltInAgent]:
    """Get a built-in agent by type."""
    return BUILT_IN_AGENTS.get(agent_type)


def list_builtin_agents() -> List[BuiltInAgent]:
    """List all built-in agents."""
    return list(BUILT_IN_AGENTS.values())