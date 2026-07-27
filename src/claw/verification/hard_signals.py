"""Pure hard-signal verifiers over recorded trajectory facts."""

from __future__ import annotations

import fnmatch
from typing import Any, Dict, List, Optional

from ..experiment.schemas import SchemaValidationError, VerificationSignal
from .base import VerificationContext


def _status_from_score(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    return "pass" if score >= 1.0 else "fail"


def _result_payload(
    context: VerificationContext, event_type: str, nested_key: str
):
    event = context.latest_event(event_type)
    if event is None:
        return None, []
    payload = context.resolve_payload(event)
    nested = payload.get(nested_key, payload)
    return nested if isinstance(nested, dict) else {}, [event.event_id]


class SchemaVerifier:
    name = "trajectory_schema"

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        try:
            context.trajectory.validate()
            return VerificationSignal(
                name=self.name,
                kind="hard",
                status="pass",
                score=1.0,
                weight=0.0,
                required=True,
                verifiable=False,
            )
        except (SchemaValidationError, ValueError) as exc:
            return VerificationSignal(
                name=self.name,
                kind="hard",
                status="fail",
                score=0.0,
                weight=0.0,
                details={"error": str(exc)},
                required=True,
                verifiable=False,
            )


class EnvironmentVerifier:
    name = "environment"

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        valid = context.facts.get("environment_valid")
        if valid is None:
            status, score = "unknown", None
        else:
            status = "pass" if bool(valid) else "fail"
            score = 1.0 if bool(valid) else 0.0
        return VerificationSignal(
            name=self.name,
            kind="hard",
            status=status,
            score=score,
            weight=context.policy.signal_weights.get(self.name, 0.0),
            details=dict(context.facts.get("environment_details") or {}),
            required=context.is_required(self.name),
            verifiable=False,
        )


class TestPassVerifier:
    name = "test_pass_rate"

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        result, evidence = _result_payload(
            context, "test_result", "test_result"
        )
        score = None
        if result is not None:
            total = int(result.get("total_tests") or 0)
            passed = int(result.get("passed_tests") or 0)
            if total > 0:
                score = max(0.0, min(1.0, passed / total))
        return VerificationSignal(
            name=self.name,
            kind="hard",
            status=_status_from_score(score),
            score=score,
            weight=context.policy.signal_weights.get(self.name, 1.0),
            evidence_event_ids=evidence,
            details={"result": result or {}},
            required=context.is_required(self.name),
            verifiable=True,
        )


class BuildVerifier:
    def __init__(self, name: str):
        self.name = name

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        raw = context.facts.get(self.name)
        status = "unknown"
        score = None
        details: Dict[str, Any] = {}
        if isinstance(raw, bool):
            status = "pass" if raw else "fail"
            score = 1.0 if raw else 0.0
        elif isinstance(raw, dict):
            details = dict(raw)
            if "passed" in raw:
                status = "pass" if raw["passed"] else "fail"
                score = 1.0 if raw["passed"] else 0.0
            elif "returncode" in raw:
                status = "pass" if int(raw["returncode"]) == 0 else "fail"
                score = 1.0 if status == "pass" else 0.0
        return VerificationSignal(
            name=self.name,
            kind="hard",
            status=status,
            score=score,
            weight=context.policy.signal_weights.get(self.name, 1.0),
            details=details,
            required=context.is_required(self.name),
            verifiable=True,
        )


class DiffScopeVerifier:
    name = "diff_scope"

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        if not context.allowed_path_patterns:
            return VerificationSignal(
                name=self.name,
                kind="hard",
                status="not_applicable",
                score=None,
                weight=context.policy.signal_weights.get(self.name, 1.0),
                required=False,
                verifiable=False,
            )
        result, evidence = _result_payload(
            context, "workspace_diff", "diff_result"
        )
        changed: Optional[List[str]] = None
        if result is not None:
            raw_changed = result.get("changed_files")
            if isinstance(raw_changed, list):
                changed = [str(item).replace("\\", "/") for item in raw_changed]
        if changed is None:
            return VerificationSignal(
                name=self.name,
                kind="hard",
                status="unknown",
                score=None,
                weight=context.policy.signal_weights.get(self.name, 1.0),
                evidence_event_ids=evidence,
                details={"allowed_patterns": context.allowed_path_patterns},
                required=context.is_required(self.name),
                verifiable=True,
            )
        violations = [
            path
            for path in changed
            if not any(
                fnmatch.fnmatch(path, pattern)
                for pattern in context.allowed_path_patterns
            )
        ]
        return VerificationSignal(
            name=self.name,
            kind="hard",
            status="fail" if violations else "pass",
            score=0.0 if violations else 1.0,
            weight=context.policy.signal_weights.get(self.name, 1.0),
            evidence_event_ids=evidence,
            details={
                "changed_files": changed,
                "violations": violations,
                "allowed_patterns": context.allowed_path_patterns,
            },
            required=context.is_required(self.name),
            verifiable=True,
        )


class ProcessPermissionVerifier:
    name = "process_permission"

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        complete = bool(context.facts.get("process_trace_complete", False))
        violations = []
        evidence = []
        for event in context.trajectory.events:
            payload = context.resolve_payload(event)
            if event.event_type == "permission_decision":
                decision = str(payload.get("decision", "")).lower()
                if decision in {"denied", "deny"} and payload.get(
                    "tool_executed", False
                ):
                    violations.append("tool executed after permission denial")
                    evidence.append(event.event_id)
            if event.event_type == "tool_call" and payload.get(
                "policy_violation", False
            ):
                violations.append(str(payload.get("violation", "policy violation")))
                evidence.append(event.event_id)
        if violations:
            status, score = "fail", 0.0
        elif complete:
            status, score = "pass", 1.0
        else:
            status, score = "unknown", None
        return VerificationSignal(
            name=self.name,
            kind="hard",
            status=status,
            score=score,
            weight=context.policy.signal_weights.get(self.name, 1.0),
            evidence_event_ids=evidence,
            details={"violations": violations, "trace_complete": complete},
            required=context.is_required(self.name),
            verifiable=True,
        )


class FormatSchemaVerifier:
    name = "format_schema"

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        valid = context.facts.get("format_valid")
        details = dict(context.facts.get("format_details") or {})
        schema = context.facts.get("output_schema")
        if valid is None and isinstance(schema, dict):
            errors: List[str] = []
            self._validate_value(
                context.facts.get("output_value"), schema, "$", errors
            )
            valid = not errors
            details.update({"schema": schema, "errors": errors})
        if valid is None:
            status, score = "unknown", None
        else:
            status = "pass" if bool(valid) else "fail"
            score = 1.0 if bool(valid) else 0.0
        event = context.latest_event("model_response")
        return VerificationSignal(
            name=self.name,
            kind="hard",
            status=status,
            score=score,
            weight=context.policy.signal_weights.get(self.name, 1.0),
            evidence_event_ids=[event.event_id] if event else [],
            details=details,
            required=context.is_required(self.name),
            verifiable=True,
        )

    @classmethod
    def _validate_value(
        cls,
        value: Any,
        schema: Dict[str, Any],
        path: str,
        errors: List[str],
    ) -> None:
        expected = schema.get("type")
        type_checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if expected in type_checks and not type_checks[expected](value):
            errors.append(f"{path}: expected {expected}")
            return
        if expected == "object" and isinstance(value, dict):
            for required in schema.get("required", []):
                if required not in value:
                    errors.append(f"{path}: missing required property {required}")
            for name, child_schema in schema.get("properties", {}).items():
                if name in value and isinstance(child_schema, dict):
                    cls._validate_value(
                        value[name], child_schema, f"{path}.{name}", errors
                    )
        if expected == "array" and isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    cls._validate_value(
                        item, item_schema, f"{path}[{index}]", errors
                    )


class TerminationVerifier:
    name = "termination_compliance"

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        termination = context.trajectory.header.termination
        if termination is None:
            return VerificationSignal(
                name=self.name,
                kind="hard",
                status="unknown",
                score=None,
                weight=0.0,
                required=True,
                verifiable=False,
            )
        passed = termination.reason == "completed"
        evidence = (
            [context.trajectory.events[-1].event_id]
            if context.trajectory.events
            else []
        )
        return VerificationSignal(
            name=self.name,
            kind="hard",
            status="pass" if passed else "fail",
            score=1.0 if passed else 0.0,
            weight=0.0,
            evidence_event_ids=evidence,
            details={
                "reason": termination.reason,
                "detail": termination.detail,
                "usage": termination.usage,
                "cost": termination.cost,
            },
            required=True,
            verifiable=False,
        )


DEFAULT_HARD_VERIFIERS = [
    SchemaVerifier(),
    EnvironmentVerifier(),
    TestPassVerifier(),
    BuildVerifier("build"),
    BuildVerifier("static_check"),
    DiffScopeVerifier(),
    ProcessPermissionVerifier(),
    FormatSchemaVerifier(),
    TerminationVerifier(),
]
