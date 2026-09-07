"""Concrete benchmark adapter backed by the real local Agent runtime."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from ..episode import EpisodeOrchestrator, RuntimeAdapter
from ..experiment.schemas import TaskSpec, canonical_hash
from ..trajectory import analyze_rollout_behavior
from ..verification import (
    VerificationContext,
    VerificationPolicy,
    VerifierPipeline,
)
from .metrics import BenchmarkEpisodeResult
from .runner import BenchmarkError


AgentFactory = Callable[[str, Dict[str, Any]], Any]
AllowedPathsResolver = Callable[[TaskSpec], List[str]]
FactsFactory = Callable[[TaskSpec, Any], Dict[str, Any]]
ReviewerFactory = Callable[[TaskSpec, Any], Any]


class LocalAgentBenchmarkAdapter:
    """Run immutable test tasks through Episode, Agent, trace, and verifier."""

    def __init__(
        self,
        episodes_root: Union[str, Path],
        *,
        project_root: Optional[Union[str, Path]] = None,
        agent_factory: AgentFactory,
        model_ref: str,
        runtime_version: str,
        prompt_version: str,
        tool_version: str,
        config_version: str,
        verifier_pipeline: Optional[VerifierPipeline] = None,
        verification_policy: Optional[VerificationPolicy] = None,
        allowed_paths_resolver: Optional[AllowedPathsResolver] = None,
        facts_factory: Optional[FactsFactory] = None,
        reviewer_factory: Optional[ReviewerFactory] = None,
        input_token_price: float = 0.0,
        output_token_price: float = 0.0,
        require_model_match: bool = True,
        episode_prefix: str = "benchmark",
        command_runner: Optional[Any] = None,
    ):
        for name, value in (
            ("model_ref", model_ref),
            ("runtime_version", runtime_version),
            ("prompt_version", prompt_version),
            ("tool_version", tool_version),
            ("config_version", config_version),
        ):
            if not str(value).strip():
                raise BenchmarkError(f"{name} must not be empty")
        if input_token_price < 0 or output_token_price < 0:
            raise BenchmarkError("token prices must be non-negative")
        if not str(episode_prefix).strip():
            raise BenchmarkError("episode_prefix must not be empty")
        self.episodes_root = Path(episodes_root).resolve()
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.agent_factory = agent_factory
        self.model_ref = model_ref
        self.runtime_version = runtime_version
        self.prompt_version = prompt_version
        self.tool_version = tool_version
        self.config_version = config_version
        self.verifier_pipeline = verifier_pipeline or VerifierPipeline()
        self.verification_policy = verification_policy or VerificationPolicy()
        self.allowed_paths_resolver = (
            allowed_paths_resolver or (lambda _task: ["**"])
        )
        self.facts_factory = facts_factory
        self.reviewer_factory = reviewer_factory
        self.input_token_price = input_token_price
        self.output_token_price = output_token_price
        self.require_model_match = require_model_match
        self.episode_prefix = str(episode_prefix)
        self.command_runner = command_runner

    def run(
        self,
        task: TaskSpec,
        inference_config: Dict[str, Any],
    ) -> BenchmarkEpisodeResult:
        task.validate()
        if task.split != "test":
            raise BenchmarkError("local benchmark adapter requires a test task")
        return self._run_verified_episode(task, inference_config)

    def _run_verified_episode(
        self,
        task: TaskSpec,
        inference_config: Dict[str, Any],
    ) -> BenchmarkEpisodeResult:
        """Run a validated task; callers must enforce their own split boundary."""
        if not isinstance(inference_config, dict):
            raise BenchmarkError("inference_config must be an object")
        max_turns = int(inference_config.get("max_turns", 100))
        if max_turns <= 0:
            raise BenchmarkError("max_turns must be positive")
        self._validate_template_ref(task.template_ref)

        started = time.monotonic()
        episode_id = f"{self.episode_prefix}-{uuid.uuid4().hex[:20]}"
        orchestrator = EpisodeOrchestrator(
            self.episodes_root,
            project_root=self.project_root,
            command_runner=self.command_runner,
        )
        orchestrator.prepare(
            task,
            episode_id=episode_id,
            runtime_config_ref=self.runtime_version,
            model_config_ref=self.model_ref,
            prompt_version=self.prompt_version,
            tool_version=self.tool_version,
        )
        assert orchestrator.workspace is not None
        agent = self.agent_factory(
            str(orchestrator.workspace),
            dict(inference_config),
        )
        if self.command_runner is not None:
            setattr(agent, "command_runner", self.command_runner)
            permissions = getattr(agent, "permissions", None)
            if isinstance(permissions, dict):
                permissions["restrict_workspace"] = True
        observed_model = str(
            getattr(getattr(agent, "client", None), "model", "")
        )
        if self.require_model_match and observed_model != self.model_ref:
            raise BenchmarkError(
                f"agent model mismatch: expected {self.model_ref!r}, "
                f"got {observed_model!r}"
            )

        runtime_adapter = RuntimeAdapter.create(
            orchestrator,
            model_version=self.model_ref,
            runtime_version=self.runtime_version,
            prompt_version=self.prompt_version,
            tool_version=self.tool_version,
            config_version=(
                f"{self.config_version}:"
                f"{canonical_hash(inference_config)[:12]}"
            ),
            task=task,
        )
        run_result = runtime_adapter.run(
            agent,
            task.prompt,
            max_turns=max_turns,
            stream=False,
        )
        trajectory = runtime_adapter.recorder.trajectory
        facts = {
            "environment_valid": True,
            "environment_details": {},
            "process_trace_complete": True,
            "format_valid": self._format_valid(trajectory),
        }
        if self.facts_factory is not None:
            facts.update(self.facts_factory(task, trajectory))
        context = VerificationContext(
            trajectory=trajectory,
            task=task,
            policy=self.verification_policy,
            artifact_store=runtime_adapter.recorder.artifact_store,
            allowed_path_patterns=list(self.allowed_paths_resolver(task)),
            facts=facts,
        )
        reviewer = (
            self.reviewer_factory(task, trajectory)
            if self.reviewer_factory is not None
            else None
        )
        report, verification_ref = runtime_adapter.verify_and_archive(
            self.verifier_pipeline,
            context,
            reviewer=reviewer,
        )
        return self._to_benchmark_result(
            task=task,
            run_result=run_result,
            runtime_adapter=runtime_adapter,
            report=report,
            verification_ref=verification_ref,
            latency_seconds=time.monotonic() - started,
        )

    def _to_benchmark_result(
        self,
        *,
        task: TaskSpec,
        run_result: Any,
        runtime_adapter: RuntimeAdapter,
        report: Any,
        verification_ref: str,
        latency_seconds: float,
    ) -> BenchmarkEpisodeResult:
        trajectory = runtime_adapter.recorder.trajectory
        signals = {signal.name: signal for signal in report.signals}
        test_signal = signals.get("test_pass_rate")
        integrity_signal = signals.get("evaluation_integrity")
        process_signal = signals.get("process_permission")
        format_signal = signals.get("format_schema")
        tool_calls = [
            event
            for event in trajectory.events
            if event.event_type == "tool_call"
        ]
        tool_results = [
            runtime_adapter.recorder.resolve_payload(event)
            for event in trajectory.events
            if event.event_type == "tool_result"
        ]
        valid_selections = sum(
            bool(payload.get("selection_valid", False))
            for payload in tool_results
        )
        valid_arguments = sum(
            bool(payload.get("arguments_valid", False))
            for payload in tool_results
        )
        usage = (
            trajectory.header.termination.usage
            if trajectory.header.termination
            else {}
        )
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        process_violations = len(
            (process_signal.details if process_signal else {}).get(
                "violations", []
            )
        )
        bad_cases = [
            str(
                item.get("primary_category")
                or item.get("category")
                or "unclassified"
            )
            for item in report.bad_cases
        ]
        return BenchmarkEpisodeResult(
            task_id=task.task_id,
            family_id=task.family_id,
            domain=task.domain,
            difficulty=task.difficulty,
            success=report.verdict == "success",
            test_pass_rate=(
                float(test_signal.score)
                if test_signal is not None and test_signal.score is not None
                else None
            ),
            tool_calls=len(tool_calls),
            valid_tool_selections=min(valid_selections, len(tool_calls)),
            valid_tool_arguments=min(valid_arguments, len(tool_calls)),
            format_valid=bool(
                format_signal is not None and format_signal.status == "pass"
            ),
            process_violations=process_violations,
            turns=sum(
                event.event_type == "model_response"
                for event in trajectory.events
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            token_cost=(
                input_tokens * self.input_token_price
                + output_tokens * self.output_token_price
            ),
            latency_seconds=max(0.0, latency_seconds),
            bad_cases=bad_cases,
            evaluation_prepared=bool(
                integrity_signal is None
                or integrity_signal.details.get("evaluation_prepared", True)
            ),
            tests_executed=bool(
                integrity_signal is None
                or integrity_signal.details.get("tests_executed", True)
            ),
            verification_ref=verification_ref,
            trajectory_ref=str(runtime_adapter.recorder.path),
            error=run_result.error,
            behavior_diagnostics=analyze_rollout_behavior(
                trajectory,
                target_path_patterns=list(self.allowed_paths_resolver(task)),
                payload_resolver=runtime_adapter.recorder.resolve_payload,
            ).to_dict(),
        )

    def _validate_template_ref(self, reference: str) -> None:
        template = Path(reference)
        if template.is_absolute():
            return
        resolved = (self.project_root / template).resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise BenchmarkError(
                f"task template escapes project root: {reference}"
            ) from exc

    @staticmethod
    def _format_valid(trajectory: Any) -> bool:
        responses = [
            event
            for event in trajectory.events
            if event.event_type == "model_response"
        ]
        if not responses:
            return False
        return all(isinstance(event.payload, dict) for event in responses)
