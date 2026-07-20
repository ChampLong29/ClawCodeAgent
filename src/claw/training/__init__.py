"""Training environment for LLM agent development."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    "AgentEnv": (".agent_env", "AgentEnv"),
    "EnvObservation": (".agent_env", "EnvObservation"),
    "SandboxManager": (".sandbox", "SandboxManager"),
    "TestResult": (".sandbox", "TestResult"),
    "DiffResult": (".sandbox", "DiffResult"),
    "CodingTask": (".tasks", "CodingTask"),
    "TaskSuite": (".tasks", "TaskSuite"),
    "RolloutRunner": (".runner", "RolloutRunner"),
    "RolloutConfig": (".runner", "RolloutConfig"),
    "RolloutResult": (".runner", "RolloutResult"),
    "DeterministicConfig": (".determinism", "DeterministicConfig"),
    "SnapshotVerifier": (".determinism", "SnapshotVerifier"),
    "DomainConfig": (".domain_config", "DomainConfig"),
    "DomainRegistry": (".domain_config", "DomainRegistry"),
    "default_registry": (".domain_config", "default_registry"),
    "ReviewerAgent": (".reviewer", "ReviewerAgent"),
    "ReviewReport": (".reviewer", "ReviewReport"),
    "ReviewScore": (".reviewer", "ReviewScore"),
    "ReviewIssue": (".reviewer", "ReviewIssue"),
    "SlimeDataAdapter": (".slime_adapter", "SlimeDataAdapter"),
    "SlimeTrainingSample": (".slime_adapter", "SlimeTrainingSample"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value


if TYPE_CHECKING:
    from .agent_env import AgentEnv, EnvObservation
    from .sandbox import SandboxManager, TestResult, DiffResult
    from .tasks import CodingTask, TaskSuite
    from .runner import RolloutRunner, RolloutConfig, RolloutResult
    from .determinism import DeterministicConfig, SnapshotVerifier
    from .domain_config import DomainConfig, DomainRegistry, default_registry
    from .reviewer import ReviewerAgent, ReviewReport, ReviewScore, ReviewIssue
    from .slime_adapter import SlimeDataAdapter, SlimeTrainingSample
