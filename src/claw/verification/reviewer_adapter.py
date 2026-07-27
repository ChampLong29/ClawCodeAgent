"""Adapter from the legacy independent reviewer to a versioned soft signal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

from ..experiment.schemas import VerificationSignal
from ..training.reviewer import ReviewReport


@dataclass
class ReviewerEvidence:
    report: Union[ReviewReport, Dict[str, Any]]
    model: str
    prompt_version: str
    session_id: str
    reviewer_version: str = "reviewer-adapter.v1"
    temperature: float = 0.0
    raw_response_ref: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ReviewerAdapter:
    signal_name = "independent_reviewer"

    def to_signal(
        self,
        evidence: ReviewerEvidence,
        *,
        weight: float,
    ) -> VerificationSignal:
        for name in ("model", "prompt_version", "session_id", "reviewer_version"):
            if not str(getattr(evidence, name)).strip():
                raise ValueError(f"reviewer {name} must not be empty")
        report = (
            evidence.report
            if isinstance(evidence.report, ReviewReport)
            else ReviewReport.from_dict(evidence.report)
        )
        score = float(report.overall_score)
        if not 0.0 <= score <= 1.0:
            raise ValueError("reviewer overall_score must be within [0, 1]")
        details = {
            "summary": report.summary,
            "issues": [issue.to_dict() for issue in report.issues],
            "dimensions": {
                name: value.to_dict()
                for name, value in report.dimensions.items()
            },
            "model": evidence.model,
            "prompt_version": evidence.prompt_version,
            "session_id": evidence.session_id,
            "reviewer_version": evidence.reviewer_version,
            "temperature": evidence.temperature,
            "raw_response_ref": evidence.raw_response_ref,
            **evidence.metadata,
        }
        return VerificationSignal(
            name=self.signal_name,
            kind="soft",
            status="pass",
            score=score,
            weight=weight,
            details=details,
            required=False,
            verifiable=False,
        )

    @staticmethod
    def metadata(evidence: ReviewerEvidence) -> Dict[str, Any]:
        return {
            "model": evidence.model,
            "prompt_version": evidence.prompt_version,
            "session_id": evidence.session_id,
            "reviewer_version": evidence.reviewer_version,
            "temperature": evidence.temperature,
            "raw_response_ref": evidence.raw_response_ref,
        }
