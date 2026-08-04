"""Deterministic, dependency-free operators for Agent training records."""

from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..dataset.converters import validate_tool_alignment
from .schemas import AgentTrainingRecord


TOOL_ALIGNMENT_OPERATOR_VERSION = "tool_alignment.v1"
LEAKAGE_GUARD_OPERATOR_VERSION = "leakage_guard.v1"
FAILURE_TAXONOMY_OPERATOR_VERSION = "failure_taxonomy.v1"
QUALITY_SCORER_OPERATOR_VERSION = "trajectory_quality.v1"
BALANCER_OPERATOR_VERSION = "domain_difficulty_balancer.v1"


def _replace_features(
    record: AgentTrainingRecord, updates: Dict[str, Any]
) -> AgentTrainingRecord:
    payload = record.to_dict()
    payload["features"].update(copy.deepcopy(updates))
    payload["content_hash"] = ""
    updated = AgentTrainingRecord.from_dict(payload)
    updated.content_hash = updated.compute_content_hash()
    updated.validate()
    return updated


def _signal(record: AgentTrainingRecord, name: str) -> Dict[str, Any]:
    value = record.verification.get("signals", {}).get(name, {})
    return value if isinstance(value, dict) else {}


def _signal_score(record: AgentTrainingRecord, name: str, *, unknown: float) -> float:
    signal = _signal(record, name)
    score = signal.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return min(1.0, max(0.0, float(score)))
    status = signal.get("status")
    if status == "pass":
        return 1.0
    if status == "fail":
        return 0.0
    return unknown


def _tool_call_count(messages: Sequence[Dict[str, Any]]) -> int:
    return sum(
        len(message.get("tool_calls") or [])
        for message in messages
        if message.get("role") == "assistant"
    )


@dataclass(frozen=True)
class OperatorDecision:
    passed: bool
    reasons: Tuple[str, ...] = ()
    evidence: Dict[str, Any] = field(default_factory=dict)


class ToolAlignmentValidator:
    version = TOOL_ALIGNMENT_OPERATOR_VERSION

    def evaluate(self, record: AgentTrainingRecord) -> OperatorDecision:
        try:
            validate_tool_alignment(record.messages)
        except ValueError as exc:
            return OperatorDecision(False, ("tool_alignment_invalid",), {"error": str(exc)})
        observed = _tool_call_count(record.messages)
        declared = record.execution.get("tool_calls")
        if isinstance(declared, int) and declared >= 0 and declared != observed:
            return OperatorDecision(
                False,
                ("tool_call_count_mismatch",),
                {"declared": declared, "observed": observed},
            )
        return OperatorDecision(True, evidence={"observed_tool_calls": observed})


class LeakageGuardOperator:
    version = LEAKAGE_GUARD_OPERATOR_VERSION

    _PATTERNS = (
        ("oracle_path", re.compile(r"(?i)(?:^|[/\\])oracles?(?:[/\\]|$)")),
        ("private_oracle", re.compile(r"(?i)private[/\\]oracle")),
        ("gold_patch", re.compile(r"(?i)gold[_-]?patch")),
        ("test_patch", re.compile(r"(?i)test[_-]?patch")),
        ("hidden_test_asset", re.compile(r"(?i)hidden[_-]?tests?(?:[/\\]|\.(?:py|json))")),
        ("test_asset_reference", re.compile(r"(?i)test[_-]?assets?(?:[/\\]|\.(?:py|json|zip))")),
    )
    _FORBIDDEN_KEYS = {
        "oracle_ref",
        "oracle_content",
        "gold_patch",
        "test_patch",
        "test_assets_ref",
        "test_assets_hash",
        "hidden_tests",
        "expected_answer",
    }

    def evaluate(self, record: AgentTrainingRecord) -> OperatorDecision:
        flags = set()
        key_hits = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() in self._FORBIDDEN_KEYS:
                        key_hits.add(str(key).lower())
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(record.messages)
        serialized = json.dumps(record.messages, ensure_ascii=False, sort_keys=True)
        for label, pattern in self._PATTERNS:
            if pattern.search(serialized):
                flags.add(label)
        if record.task.get("split") == "test":
            flags.add("test_split")
        flags.update(f"forbidden_key:{name}" for name in key_hits)
        ordered = tuple(sorted(flags))
        return OperatorDecision(
            not ordered,
            ordered,
            {"flag_count": len(ordered), "flags": list(ordered)},
        )


class FailureTaxonomyAnnotator:
    version = FAILURE_TAXONOMY_OPERATOR_VERSION

    _SIGNAL_MAPPING = (
        ("format_schema", "format_failure"),
        ("process_permission", "permission_denied"),
        ("environment", "environment_failure"),
        ("diff_scope", "diff_violation"),
        ("test_pass_rate", "test_failure"),
        ("termination_compliance", "invalid_termination"),
    )

    def classify(self, record: AgentTrainingRecord) -> Optional[str]:
        current = record.features.get("failure_type")
        if isinstance(current, str) and current:
            return current
        termination = record.execution.get("termination_reason")
        if termination == "timeout":
            return "timeout"
        for signal_name, category in self._SIGNAL_MAPPING:
            if _signal(record, signal_name).get("status") == "fail":
                return category
        if termination not in {None, "completed"}:
            return "invalid_termination"
        if record.verification.get("verdict") == "failure":
            return "tool_error"
        return None

    def apply(self, record: AgentTrainingRecord) -> AgentTrainingRecord:
        return _replace_features(
            record,
            {
                "failure_type": self.classify(record),
                "failure_taxonomy_version": self.version,
            },
        )


@dataclass(frozen=True)
class QualityPolicy:
    version: str = QUALITY_SCORER_OPERATOR_VERSION
    correctness_weight: float = 0.45
    diff_safety_weight: float = 0.20
    reviewer_quality_weight: float = 0.15
    tool_validity_weight: float = 0.10
    efficiency_weight: float = 0.10
    efficient_action_target: int = 32
    maximum_action_budget: int = 128

    def validate(self) -> None:
        weights = (
            self.correctness_weight,
            self.diff_safety_weight,
            self.reviewer_quality_weight,
            self.tool_validity_weight,
            self.efficiency_weight,
        )
        if any(weight < 0 for weight in weights):
            raise ValueError("quality weights must be non-negative")
        if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("quality weights must sum to 1.0")
        if self.efficient_action_target < 0:
            raise ValueError("efficient_action_target must be non-negative")
        if self.maximum_action_budget <= self.efficient_action_target:
            raise ValueError("maximum_action_budget must exceed efficient_action_target")


class TrajectoryQualityScorer:
    def __init__(self, policy: Optional[QualityPolicy] = None):
        self.policy = policy or QualityPolicy()
        self.policy.validate()
        self.version = self.policy.version

    def _efficiency(self, record: AgentTrainingRecord) -> float:
        model_calls = record.execution.get("model_calls")
        tool_calls = record.execution.get("tool_calls")
        if not isinstance(model_calls, int) or not isinstance(tool_calls, int):
            return 0.5
        actions = max(0, model_calls) + max(0, tool_calls)
        target = self.policy.efficient_action_target
        maximum = self.policy.maximum_action_budget
        if actions <= target:
            return 1.0
        return max(0.0, 1.0 - (actions - target) / (maximum - target))

    def score(
        self,
        record: AgentTrainingRecord,
        *,
        alignment: Optional[OperatorDecision] = None,
    ) -> Tuple[float, Dict[str, float]]:
        reviewer = record.verification.get("reviewer_score")
        reviewer_score = (
            min(1.0, max(0.0, float(reviewer)))
            if isinstance(reviewer, (int, float)) and not isinstance(reviewer, bool)
            else 0.5
        )
        components = {
            "correctness": _signal_score(record, "test_pass_rate", unknown=0.0),
            "diff_safety": _signal_score(record, "diff_scope", unknown=0.0),
            "reviewer_quality": reviewer_score,
            "tool_validity": 1.0 if alignment is None or alignment.passed else 0.0,
            "efficiency": self._efficiency(record),
        }
        hard_gate = 1.0 if record.verification.get("hard_gate_passed") else 0.0
        score = hard_gate * (
            self.policy.correctness_weight * components["correctness"]
            + self.policy.diff_safety_weight * components["diff_safety"]
            + self.policy.reviewer_quality_weight * components["reviewer_quality"]
            + self.policy.tool_validity_weight * components["tool_validity"]
            + self.policy.efficiency_weight * components["efficiency"]
        )
        return round(score, 8), components

    def apply(
        self,
        record: AgentTrainingRecord,
        *,
        alignment: Optional[OperatorDecision] = None,
    ) -> AgentTrainingRecord:
        score, components = self.score(record, alignment=alignment)
        return _replace_features(
            record,
            {
                "quality_score": score,
                "quality_components": components,
                "quality_policy_version": self.version,
            },
        )


def _length_bucket(record: AgentTrainingRecord) -> str:
    size = len(record.messages)
    if size <= 8:
        return "short"
    if size <= 24:
        return "medium"
    return "long"


def _tool_signature(record: AgentTrainingRecord) -> str:
    names = []
    for message in record.messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function")
            name = function.get("name") if isinstance(function, dict) else call.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return "+".join(sorted(set(names))) or "no_tools"


class DomainDifficultyBalancer:
    version = BALANCER_OPERATOR_VERSION

    def __init__(self, *, minimum_weight: float = 0.25, maximum_weight: float = 4.0):
        if minimum_weight <= 0 or maximum_weight < minimum_weight:
            raise ValueError("invalid balance weight bounds")
        self.minimum_weight = minimum_weight
        self.maximum_weight = maximum_weight

    def _stratum(self, record: AgentTrainingRecord) -> Tuple[str, ...]:
        return (
            str(record.task.get("domain")),
            str(record.task.get("task_type")),
            str(record.task.get("difficulty")),
            str(record.features.get("failure_type") or "success"),
            _length_bucket(record),
            _tool_signature(record),
        )

    def apply(
        self, records: Iterable[AgentTrainingRecord]
    ) -> Tuple[List[AgentTrainingRecord], Dict[str, Any]]:
        items = list(records)
        if not items:
            return [], {"version": self.version, "strata": {}, "record_count": 0}
        strata = [self._stratum(record) for record in items]
        counts = Counter(strata)
        raw_weights = [len(items) / (len(counts) * counts[key]) for key in strata]
        bounded = [
            min(self.maximum_weight, max(self.minimum_weight, weight))
            for weight in raw_weights
        ]
        mean = sum(bounded) / len(bounded)
        normalized = [weight / mean for weight in bounded]
        total = sum(normalized)
        output = []
        for record, stratum, weight in zip(items, strata, normalized):
            output.append(
                _replace_features(
                    record,
                    {
                        "sample_weight": round(weight, 8),
                        "sample_probability": round(weight / total, 10),
                        "balance_stratum": list(stratum),
                        "balance_policy_version": self.version,
                    },
                )
            )
        report_counts = {"|".join(key): value for key, value in sorted(counts.items())}
        return output, {
            "version": self.version,
            "record_count": len(items),
            "strata": report_counts,
            "minimum_weight": self.minimum_weight,
            "maximum_weight": self.maximum_weight,
        }


OPERATOR_VERSIONS = {
    "tool_alignment": TOOL_ALIGNMENT_OPERATOR_VERSION,
    "leakage_guard": LEAKAGE_GUARD_OPERATOR_VERSION,
    "failure_taxonomy": FAILURE_TAXONOMY_OPERATOR_VERSION,
    "quality_scorer": QUALITY_SCORER_OPERATOR_VERSION,
    "balancer": BALANCER_OPERATOR_VERSION,
}
