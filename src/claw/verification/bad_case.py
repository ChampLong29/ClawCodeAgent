"""Rule-first bad-case taxonomy with append-only human revisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..experiment.schemas import VerificationReport, VerificationSignal
from ..trajectory.schema import Trajectory


BAD_CASE_CATEGORIES = {
    "task_understanding",
    "planning_or_phase_skip",
    "wrong_tool_or_arguments",
    "tool_execution_failure",
    "context_loss",
    "permission_violation",
    "over_editing",
    "test_failure",
    "format_or_schema",
    "budget_or_timeout",
    "review_quality",
    "environment_or_infra",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BadCaseRevision:
    primary_category: str
    secondary_categories: List[str]
    author: str
    reason: str
    created_at: str = field(default_factory=_now)

    def validate(self) -> None:
        if self.primary_category not in BAD_CASE_CATEGORIES:
            raise ValueError(f"unknown bad-case category: {self.primary_category}")
        if any(item not in BAD_CASE_CATEGORIES for item in self.secondary_categories):
            raise ValueError("unknown secondary bad-case category")
        if not self.author or not self.reason:
            raise ValueError("bad-case revision requires author and reason")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BadCaseRevision":
        revision = cls(**data)
        revision.validate()
        return revision


@dataclass
class BadCaseRecord:
    primary_category: str
    secondary_categories: List[str]
    evidence_event_ids: List[str]
    retryable: bool
    suggestion: str
    source: str = "automatic"
    revisions: List[BadCaseRevision] = field(default_factory=list)

    def validate(self) -> None:
        if self.primary_category not in BAD_CASE_CATEGORIES:
            raise ValueError(f"unknown bad-case category: {self.primary_category}")
        if any(item not in BAD_CASE_CATEGORIES for item in self.secondary_categories):
            raise ValueError("unknown secondary bad-case category")
        for revision in self.revisions:
            revision.validate()

    def revise(
        self,
        *,
        primary_category: str,
        secondary_categories: Optional[List[str]] = None,
        author: str,
        reason: str,
    ) -> None:
        revision = BadCaseRevision(
            primary_category=primary_category,
            secondary_categories=list(secondary_categories or []),
            author=author,
            reason=reason,
        )
        revision.validate()
        self.revisions.append(revision)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BadCaseRecord":
        payload = dict(data)
        payload.pop("effective_primary_category", None)
        payload["revisions"] = [
            BadCaseRevision.from_dict(item)
            for item in payload.get("revisions", [])
        ]
        record = cls(**payload)
        record.validate()
        return record

    @property
    def effective_primary_category(self) -> str:
        return (
            self.revisions[-1].primary_category
            if self.revisions
            else self.primary_category
        )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["revisions"] = [item.to_dict() for item in self.revisions]
        data["effective_primary_category"] = self.effective_primary_category
        return data


class BadCaseClassifier:
    _PRIORITY = [
        "environment_or_infra",
        "permission_violation",
        "budget_or_timeout",
        "format_or_schema",
        "over_editing",
        "test_failure",
        "tool_execution_failure",
        "planning_or_phase_skip",
        "review_quality",
        "wrong_tool_or_arguments",
        "context_loss",
        "task_understanding",
    ]

    _SUGGESTIONS = {
        "environment_or_infra": "Repair the task environment and rerun verification.",
        "permission_violation": "Enforce phase tool visibility and permission checks.",
        "budget_or_timeout": "Reduce scope or adjust the controlled execution budget.",
        "format_or_schema": "Validate structured output before terminating the episode.",
        "over_editing": "Restrict edits to task-authorized path patterns.",
        "test_failure": "Use failing test evidence to correct the implementation.",
        "tool_execution_failure": "Inspect tool arguments and execution diagnostics.",
        "planning_or_phase_skip": "Restore the required phase sequence before rerun.",
        "review_quality": "Address reviewer issues after hard signals pass.",
        "wrong_tool_or_arguments": "Select a valid tool and schema-conformant arguments.",
        "context_loss": "Restore the relevant checkpoint and compacted context.",
        "task_understanding": "Re-evaluate task constraints and acceptance criteria.",
    }

    def classify(
        self,
        report: VerificationReport,
        trajectory: Trajectory,
    ) -> Optional[BadCaseRecord]:
        low_reviewer = any(
            signal.name == "independent_reviewer"
            and signal.score is not None
            and signal.score < 0.7
            for signal in report.signals
        )
        if report.verdict == "success" and not low_reviewer:
            return None
        candidates: Dict[str, List[str]] = {}
        for signal in report.signals:
            self._from_signal(signal, candidates)
        self._from_trajectory(trajectory, candidates)
        if not candidates:
            candidates["task_understanding"] = []

        ordered = [item for item in self._PRIORITY if item in candidates]
        primary = ordered[0]
        secondary = ordered[1:]
        evidence = []
        for category in ordered:
            evidence.extend(candidates[category])
        evidence = list(dict.fromkeys(evidence))
        retryable = primary not in {"permission_violation"}
        return BadCaseRecord(
            primary_category=primary,
            secondary_categories=secondary,
            evidence_event_ids=evidence,
            retryable=retryable,
            suggestion=self._SUGGESTIONS[primary],
        )

    @staticmethod
    def _from_signal(
        signal: VerificationSignal,
        candidates: Dict[str, List[str]],
    ) -> None:
        if (
            signal.name == "independent_reviewer"
            and signal.score is not None
            and signal.score < 0.7
        ):
            candidates.setdefault("review_quality", []).extend(
                signal.evidence_event_ids
            )
            return
        if signal.status not in {"fail", "unknown"}:
            return
        if signal.status == "unknown" and not signal.required:
            return
        mapping = {
            "trajectory_schema": "format_or_schema",
            "environment": "environment_or_infra",
            "test_pass_rate": "test_failure",
            "build": "environment_or_infra",
            "static_check": "format_or_schema",
            "diff_scope": "over_editing",
            "process_permission": "permission_violation",
            "format_schema": "format_or_schema",
            "termination_compliance": "budget_or_timeout",
            "independent_reviewer": "review_quality",
        }
        category = mapping.get(signal.name)
        if category:
            candidates.setdefault(category, []).extend(signal.evidence_event_ids)

    @staticmethod
    def _from_trajectory(
        trajectory: Trajectory,
        candidates: Dict[str, List[str]],
    ) -> None:
        termination = trajectory.header.termination
        if termination and termination.reason in {"timeout", "budget_exceeded"}:
            candidates.setdefault("budget_or_timeout", [])
        for event in trajectory.events:
            if event.event_type == "tool_result":
                payload = event.payload
                failed = payload.get("exit_code") not in {None, 0} or bool(
                    payload.get("error")
                )
                if failed:
                    candidates.setdefault("tool_execution_failure", []).append(
                        event.event_id
                    )
            if event.event_type == "runtime_error":
                candidates.setdefault("environment_or_infra", []).append(
                    event.event_id
                )
