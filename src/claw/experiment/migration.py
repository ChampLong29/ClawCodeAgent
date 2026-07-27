"""Compatibility adapters from legacy training records to versioned schemas."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .schemas import (
    TaskSpec,
    VerificationReport,
    VerificationSignal,
    canonical_hash,
)
from ..trajectory.schema import stable_id


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    raise TypeError("legacy record must be a dictionary or expose to_dict()")


def coding_task_to_spec(
    task: Any,
    *,
    task_version: str = "1",
    family_id: Optional[str] = None,
    domain: str = "legacy",
    split: str = "train",
    source: str = "legacy-training-suite",
    license_name: str = "unknown",
    template_hash: Optional[str] = None,
) -> TaskSpec:
    """Convert the existing ``CodingTask`` while marking inferred metadata."""

    data = _as_dict(task)
    task_id = str(data.get("id") or data.get("task_id") or "")
    template_ref = str(data.get("template_dir") or f"legacy-inline:{task_id}")
    inferred_template_hash = template_hash or canonical_hash(
        {
            "template_ref": template_ref,
            "ground_truth_files": data.get("ground_truth_files") or {},
        }
    )
    spec = TaskSpec(
        task_id=task_id,
        task_version=task_version,
        family_id=family_id or task_id,
        domain=domain,
        task_type=str(data.get("type") or "add_feature"),
        difficulty=str(data.get("difficulty") or "easy"),
        split=split,
        prompt=str(data.get("prompt") or ""),
        template_ref=template_ref,
        template_hash=inferred_template_hash,
        initial_checks=list(data.get("initial_checks") or data.get("test_commands") or []),
        test_commands=list(data.get("test_commands") or []),
        timeout_seconds=float(data.get("timeout_seconds") or 300.0),
        source=source,
        license=license_name,
        resource_limits=dict(data.get("resource_limits") or {}),
        tags=list(data.get("tags") or []) + ["migrated-from-coding-task.v1"],
    )
    spec.content_hash = spec.compute_content_hash()
    spec.validate()
    return spec


def rollout_result_to_verification(
    result: Any,
    *,
    trajectory_ref: str,
    verifier_bundle_version: str = "legacy-rollout-verifier.v1",
) -> VerificationReport:
    """Preserve legacy evaluation signals outside the immutable trajectory."""

    data = _as_dict(result)
    signals = []

    test_result = data.get("test_result")
    if isinstance(test_result, dict):
        passed = int(test_result.get("passed_tests") or 0)
        total = int(test_result.get("total_tests") or 0)
        score = passed / total if total > 0 else None
        signals.append(
            VerificationSignal(
                name="test_pass_rate",
                kind="hard",
                status=(
                    "pass"
                    if score == 1.0
                    else "fail"
                    if score is not None
                    else "unknown"
                ),
                score=score,
                details={"legacy_result": test_result},
            )
        )

    diff_result = data.get("diff_result")
    if isinstance(diff_result, dict):
        matches = int(diff_result.get("matches") or 0)
        total_files = int(
            diff_result.get("total_files") or diff_result.get("total") or 0
        )
        score = matches / total_files if total_files > 0 else None
        signals.append(
            VerificationSignal(
                name="diff_accuracy",
                kind="hard",
                status=(
                    "pass"
                    if score == 1.0
                    else "fail"
                    if score is not None
                    else "unknown"
                ),
                score=score,
                details={"legacy_result": diff_result},
            )
        )

    review = data.get("review_report")
    if isinstance(review, dict):
        review_score = review.get("overall_score")
        signals.append(
            VerificationSignal(
                name="independent_reviewer",
                kind="soft",
                status="pass" if review_score is not None else "unknown",
                score=float(review_score) if review_score is not None else None,
                details={"legacy_report": review},
            )
        )

    hard_signals = [signal for signal in signals if signal.kind == "hard"]
    verifiable = any(signal.status in {"pass", "fail"} for signal in hard_signals)
    hard_gate_passed = verifiable and all(
        signal.status == "pass"
        for signal in hard_signals
        if signal.status in {"pass", "fail"}
    )
    verdict = (
        "not_verifiable"
        if not verifiable
        else "success"
        if hard_gate_passed
        else "failure"
    )
    aggregate = data.get("reward")
    report = VerificationReport(
        report_id=stable_id(
            "verify",
            {
                "trajectory_ref": trajectory_ref,
                "signals": [signal.to_dict() for signal in signals],
            },
        ),
        trajectory_ref=trajectory_ref,
        verifier_bundle_version=verifier_bundle_version,
        verdict=verdict,
        hard_gate_passed=hard_gate_passed,
        signals=signals,
        aggregate_score=float(aggregate) if aggregate is not None else None,
        reviewer_metadata=(
            {"source": "legacy-inline-review"} if review is not None else {}
        ),
    )
    report.validate()
    return report


__all__ = ["coding_task_to_spec", "rollout_result_to_verification"]
