"""Query engine - Facade that drives LocalCodingAgent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .agent_runtime import LocalCodingAgent
from .agent_types import AgentRunResult, BudgetConfig, ModelConfig
from .session_store import save_agent_session, load_agent_session


@dataclass
class QueryEngineConfig:
    """Configuration for the query engine."""
    model: Optional[ModelConfig] = None
    budget: Optional[BudgetConfig] = None
    permissions: Optional[Dict[str, Any]] = None
    permission_callback: Optional[Callable] = None
    max_turns: int = 100
    stream: bool = False


class QueryEngine:
    """Facade that drives LocalCodingAgent.

    This is the main entry point for executing agent queries.
    It wraps LocalCodingAgent with simplified API.
    """

    def __init__(
        self,
        cwd: str,
        config: Optional[QueryEngineConfig] = None,
    ):
        self.cwd = cwd
        self.config = config or QueryEngineConfig()
        self._agent: Optional[LocalCodingAgent] = None

    def query(
        self,
        prompt: str,
        session_id: Optional[str] = None,
    ) -> AgentRunResult:
        """Execute a query with the agent.

        Args:
            prompt: The user prompt
            session_id: Optional session ID for resume

        Returns:
            AgentRunResult with the response
        """
        if session_id:
            self._agent = LocalCodingAgent.from_session(
                session_id=session_id,
                cwd=self.cwd,
                model_config=self.config.model,
                budget=self.config.budget,
            )
            self._agent.permissions = self.config.permissions
            self._agent.permission_callback = self.config.permission_callback
            result = self._agent.resume(prompt, stream=self.config.stream)
        else:
            self._agent = LocalCodingAgent(
                cwd=self.cwd,
                model_config=self.config.model,
                budget=self.config.budget,
                permissions=self.config.permissions,
            )
            self._agent.permission_callback = self.config.permission_callback
            result = self._agent.run(prompt, max_turns=self.config.max_turns, stream=self.config.stream)

        # Persist session
        if self._agent and self._agent.session:
            save_agent_session(self._agent.session, self.cwd)

        return result

    def query_with_tools(
        self,
        prompt: str,
        allowed_tools: List[str],
        session_id: Optional[str] = None,
    ) -> AgentRunResult:
        """Execute a query with restricted tools.

        Args:
            prompt: The user prompt
            allowed_tools: List of allowed tool names
            session_id: Optional session ID for resume

        Returns:
            AgentRunResult with the response
        """
        permissions = self.config.permissions or {}
        permissions["allowed_tools"] = allowed_tools

        config = QueryEngineConfig(
            model=self.config.model,
            budget=self.config.budget,
            permissions=permissions,
            max_turns=self.config.max_turns,
            stream=self.config.stream,
        )

        engine = QueryEngine(self.cwd, config)
        return engine.query(prompt, session_id)

    def get_agent_state(self) -> Optional[Dict[str, Any]]:
        """Get current agent state."""
        if self._agent:
            return self._agent.get_state()
        return None


def run_query(
    prompt: str,
    cwd: str,
    session_id: Optional[str] = None,
    model_name: Optional[str] = None,
    budget: Optional[BudgetConfig] = None,
    stream: bool = False,
    max_turns: Optional[int] = None,
) -> AgentRunResult:
    """Simple function to run a query.

    This is a convenience function for quick queries.
    """
    model_config = None
    if model_name:
        model_config = ModelConfig(name=model_name)

    config = QueryEngineConfig(
        model=model_config,
        budget=budget,
        stream=stream,
        max_turns=max_turns or 100,
    )

    engine = QueryEngine(cwd, config)
    return engine.query(prompt, session_id)