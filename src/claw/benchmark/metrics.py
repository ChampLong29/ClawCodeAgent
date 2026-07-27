"""Episode-level benchmark facts and deterministic aggregate metrics."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..experiment.schemas import SchemaValidationError


@dataclass
class BenchmarkEpisodeResult:
    """One preserved benchmark outcome, including failures."""

    task_id: str
    family_id: str
    domain: str
    difficulty: str
    success: bool
    test_pass_rate: float
    tool_calls: int
    valid_tool_selections: int
    valid_tool_arguments: int
    format_valid: bool
    process_violations: int
    turns: int
    input_tokens: int
    output_tokens: int
    token_cost: float
    latency_seconds: float
    bad_cases: List[str] = field(default_factory=list)
    verification_ref: Optional[str] = None
    trajectory_ref: Optional[str] = None
    error: Optional[str] = None

    def validate(self) -> None:
        for name in ("task_id", "family_id", "domain", "difficulty"):
            if not str(getattr(self, name)).strip():
                raise SchemaValidationError(f"{name} must not be empty")
        if not 0.0 <= self.test_pass_rate <= 1.0:
            raise SchemaValidationError("test_pass_rate must be within [0, 1]")
        for name in (
            "tool_calls",
            "valid_tool_selections",
            "valid_tool_arguments",
            "process_violations",
            "turns",
            "input_tokens",
            "output_tokens",
        ):
            if int(getattr(self, name)) < 0:
                raise SchemaValidationError(f"{name} must be non-negative")
        if self.valid_tool_selections > self.tool_calls:
            raise SchemaValidationError(
                "valid_tool_selections cannot exceed tool_calls"
            )
        if self.valid_tool_arguments > self.tool_calls:
            raise SchemaValidationError(
                "valid_tool_arguments cannot exceed tool_calls"
            )
        if self.token_cost < 0 or self.latency_seconds < 0:
            raise SchemaValidationError("cost and latency must be non-negative")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkEpisodeResult":
        result = cls(**data)
        result.validate()
        return result


def bad_case_distribution(
    results: List[BenchmarkEpisodeResult],
) -> Dict[str, int]:
    return dict(
        sorted(Counter(case for result in results for case in result.bad_cases).items())
    )


def cost_summary(results: List[BenchmarkEpisodeResult]) -> Dict[str, Any]:
    total_input = sum(result.input_tokens for result in results)
    total_output = sum(result.output_tokens for result in results)
    total_cost = sum(result.token_cost for result in results)
    total_latency = sum(result.latency_seconds for result in results)
    count = len(results)
    return {
        "episodes": count,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "token_cost": total_cost,
        "latency_seconds": total_latency,
        "average_tokens": (total_input + total_output) / count if count else 0.0,
        "average_cost": total_cost / count if count else 0.0,
        "average_latency_seconds": total_latency / count if count else 0.0,
    }


def _aggregate(results: List[BenchmarkEpisodeResult]) -> Dict[str, Any]:
    count = len(results)
    tool_calls = sum(result.tool_calls for result in results)
    successful = sum(result.success for result in results)
    format_valid = sum(result.format_valid for result in results)
    violated = sum(result.process_violations > 0 for result in results)
    costs = cost_summary(results)
    return {
        "sample_count": count,
        "task_success_rate": successful / count if count else 0.0,
        "test_pass_rate": (
            sum(result.test_pass_rate for result in results) / count
            if count
            else 0.0
        ),
        "tool_selection_validity": (
            sum(result.valid_tool_selections for result in results) / tool_calls
            if tool_calls
            else None
        ),
        "tool_argument_validity": (
            sum(result.valid_tool_arguments for result in results) / tool_calls
            if tool_calls
            else None
        ),
        "schema_format_validity": format_valid / count if count else 0.0,
        "process_violation_rate": violated / count if count else 0.0,
        "average_turns": (
            sum(result.turns for result in results) / count if count else 0.0
        ),
        "average_tokens": costs["average_tokens"],
        "average_token_cost": costs["average_cost"],
        "average_latency_seconds": costs["average_latency_seconds"],
        "bad_case_distribution": bad_case_distribution(results),
    }


def compute_metrics(results: List[BenchmarkEpisodeResult]) -> Dict[str, Any]:
    """Compute overall metrics plus required domain/difficulty slices."""
    for result in results:
        result.validate()
    metrics = _aggregate(results)
    domain_values = sorted({result.domain for result in results})
    difficulty_values = sorted({result.difficulty for result in results})
    metrics["by_domain"] = {
        value: _aggregate([result for result in results if result.domain == value])
        for value in domain_values
    }
    metrics["by_difficulty"] = {
        value: _aggregate(
            [result for result in results if result.difficulty == value]
        )
        for value in difficulty_values
    }
    return metrics
