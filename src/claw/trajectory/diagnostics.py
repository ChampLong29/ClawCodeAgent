"""Deterministic behavior diagnostics derived from trajectory events."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from .schema import Trajectory, TrajectoryEvent


DIAGNOSTICS_SCHEMA_VERSION = "rollout_behavior_diagnostics.v3"

_DIRECT_MUTATION_TOOLS = {"write_file", "edit_file", "multi_edit", "apply_patch"}
_PATH_INSPECTION_TOOLS = {
    "read_file",
    "code_outline",
    "list_dir",
    "glob_search",
    "grep_search",
}


def _normalize_path(value: str) -> str:
    return value.strip().replace("\\", "/").lstrip("./")


def _matches_target(path: str, patterns: Sequence[str]) -> bool:
    normalized = _normalize_path(path)
    for pattern in patterns:
        candidate = _normalize_path(pattern)
        if not candidate or candidate == "**":
            continue
        if fnmatch.fnmatch(normalized, candidate):
            return True
        fixed_prefix = candidate.split("*", 1)[0].rstrip("/")
        if fixed_prefix and (
            normalized == fixed_prefix or normalized.startswith(fixed_prefix + "/")
        ):
            return True
    return False


def _arguments(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = payload.get("arguments", {})
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return {"command": raw}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _argument_paths(arguments: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for name in ("path", "file_path", "directory", "cwd"):
        value = arguments.get(name)
        if isinstance(value, str) and value.strip():
            normalized = _normalize_path(value)
            if normalized:
                paths.append(normalized)
    return paths


@dataclass(frozen=True)
class RolloutBehaviorDiagnostics:
    """Observable timing and exploration facts; no semantic quality judgement."""

    model_turns: int
    tool_calls: int
    direct_mutation_calls: int
    path_inspection_calls: int
    failed_tool_calls: int
    first_target_path_turn: Optional[int]
    first_direct_mutation_turn: Optional[int]
    target_to_mutation_turn_lag: Optional[int]
    tool_calls_before_first_mutation: int
    investigation_without_edit_ratio: float
    inspected_paths: List[str]
    target_path_inspection_calls: int
    off_target_path_inspection_calls: int
    completion_reminder_turn: Optional[int]
    completion_critical_turn: Optional[int]
    implementation_deadline_turn: Optional[int]
    implementation_escalation_turn: Optional[int]
    post_edit_contract_guidance_turn: Optional[int]
    tool_calls_after_completion_critical: int
    terminated_without_direct_mutation: bool
    schema_version: str = DIAGNOSTICS_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def analyze_rollout_behavior(
    trajectory: Trajectory,
    *,
    target_path_patterns: Sequence[str] = (),
    payload_resolver: Optional[Callable[[TrajectoryEvent], Dict[str, Any]]] = None,
) -> RolloutBehaviorDiagnostics:
    """Project append-only events into comparable rollout behavior signals.

    ``direct_mutation`` intentionally covers only explicit file-edit tools. Shell
    commands may have side effects, so the resulting signal must not be read as a
    proof that the workspace was unchanged.
    """

    trajectory.validate()
    resolve = payload_resolver or (lambda event: event.payload)
    turn = 0
    first_target_turn: Optional[int] = None
    first_mutation_turn: Optional[int] = None
    reminder_turn: Optional[int] = None
    critical_turn: Optional[int] = None
    implementation_deadline_turn: Optional[int] = None
    implementation_escalation_turn: Optional[int] = None
    post_edit_contract_guidance_turn: Optional[int] = None
    tool_calls = 0
    mutation_calls = 0
    inspection_calls = 0
    target_inspections = 0
    off_target_inspections = 0
    failed_calls = 0
    tool_calls_before_mutation = 0
    tool_calls_after_critical = 0
    inspected_paths = set()

    for event in trajectory.events:
        payload = resolve(event)
        if event.event_type == "model_response":
            turn += 1
            continue
        if event.event_type == "runtime_guidance":
            guidance_type = payload.get("guidance_type")
            if guidance_type == "completion_reminder" and reminder_turn is None:
                reminder_turn = turn
            elif guidance_type == "completion_critical" and critical_turn is None:
                critical_turn = turn
            elif (
                guidance_type == "implementation_deadline"
                and implementation_deadline_turn is None
            ):
                implementation_deadline_turn = turn
            elif (
                guidance_type == "implementation_escalation"
                and implementation_escalation_turn is None
            ):
                implementation_escalation_turn = turn
            elif (
                guidance_type == "post_edit_contract"
                and post_edit_contract_guidance_turn is None
            ):
                post_edit_contract_guidance_turn = turn
            continue
        if event.event_type == "tool_result":
            if not bool(payload.get("ok", False)):
                failed_calls += 1
            continue
        if event.event_type != "tool_call":
            continue

        tool_calls += 1
        if first_mutation_turn is None:
            tool_calls_before_mutation += 1
        if critical_turn is not None:
            tool_calls_after_critical += 1

        tool_name = str(payload.get("tool_name") or "")
        arguments = _arguments(payload)
        serialized_arguments = json.dumps(
            arguments, ensure_ascii=False, sort_keys=True
        ).replace("\\", "/")
        target_mentioned = any(
            _normalize_path(pattern).rstrip("*") in serialized_arguments
            for pattern in target_path_patterns
            if _normalize_path(pattern).rstrip("*") not in {"", "/"}
        )
        if target_mentioned and first_target_turn is None:
            first_target_turn = turn

        if tool_name in _DIRECT_MUTATION_TOOLS:
            mutation_calls += 1
            if first_mutation_turn is None:
                first_mutation_turn = turn
                tool_calls_before_mutation -= 1

        if tool_name in _PATH_INSPECTION_TOOLS:
            inspection_calls += 1
            paths = _argument_paths(arguments)
            inspected_paths.update(paths)
            if paths and any(
                _matches_target(path, target_path_patterns) for path in paths
            ):
                target_inspections += 1
                if first_target_turn is None:
                    first_target_turn = turn
            elif paths and target_path_patterns:
                off_target_inspections += 1

    if first_mutation_turn is None:
        tool_calls_before_mutation = tool_calls
    ratio = tool_calls_before_mutation / tool_calls if tool_calls else 0.0
    lag = (
        first_mutation_turn - first_target_turn
        if first_mutation_turn is not None and first_target_turn is not None
        else None
    )
    return RolloutBehaviorDiagnostics(
        model_turns=turn,
        tool_calls=tool_calls,
        direct_mutation_calls=mutation_calls,
        path_inspection_calls=inspection_calls,
        failed_tool_calls=failed_calls,
        first_target_path_turn=first_target_turn,
        first_direct_mutation_turn=first_mutation_turn,
        target_to_mutation_turn_lag=lag,
        tool_calls_before_first_mutation=tool_calls_before_mutation,
        investigation_without_edit_ratio=ratio,
        inspected_paths=sorted(inspected_paths),
        target_path_inspection_calls=target_inspections,
        off_target_path_inspection_calls=off_target_inspections,
        completion_reminder_turn=reminder_turn,
        completion_critical_turn=critical_turn,
        implementation_deadline_turn=implementation_deadline_turn,
        implementation_escalation_turn=implementation_escalation_turn,
        post_edit_contract_guidance_turn=post_edit_contract_guidance_turn,
        tool_calls_after_completion_critical=tool_calls_after_critical,
        terminated_without_direct_mutation=mutation_calls == 0,
    )
