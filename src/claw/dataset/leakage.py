"""Task-family, split, and oracle leakage detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List


@dataclass
class LeakageIssue:
    kind: str
    task_ids: List[str]
    details: Dict[str, Any] = field(default_factory=dict)
    severity: str = "error"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LeakageReport:
    passed: bool
    issues: List[LeakageIssue]
    checked_tasks: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "checked_tasks": self.checked_tasks,
            "issues": [item.to_dict() for item in self.issues],
        }


class LeakageError(ValueError):
    def __init__(self, report: LeakageReport):
        self.report = report
        super().__init__(
            "dataset leakage check failed: "
            + ", ".join(item.kind for item in report.issues)
        )


def check_leakage(records: Iterable[Any], *, target_split: str) -> LeakageReport:
    records = list(records)
    issues: List[LeakageIssue] = []
    families: Dict[str, Dict[str, List[str]]] = {}
    for record in records:
        task = record.task
        families.setdefault(task.family_id, {}).setdefault(task.split, []).append(
            task.task_id
        )
        if task.split != target_split:
            issues.append(
                LeakageIssue(
                    kind=(
                        "test_split_in_training_data"
                        if task.split == "test" and target_split != "test"
                        else "split_mismatch"
                    ),
                    task_ids=[task.task_id],
                    details={
                        "target_split": target_split,
                        "task_split": task.split,
                    },
                )
            )
        oracle = task.oracle_ref
        if oracle:
            serialized = str(record.messages)
            if oracle in serialized:
                issues.append(
                    LeakageIssue(
                        kind="oracle_reference_exposed",
                        task_ids=[task.task_id],
                        details={"oracle_ref": oracle},
                    )
                )
    for family_id, split_tasks in families.items():
        if len(split_tasks) > 1:
            task_ids = [
                task_id
                for values in split_tasks.values()
                for task_id in values
            ]
            issues.append(
                LeakageIssue(
                    kind="family_cross_split",
                    task_ids=sorted(set(task_ids)),
                    details={
                        "family_id": family_id,
                        "splits": sorted(split_tasks),
                    },
                )
            )
    return LeakageReport(
        passed=not any(item.severity == "error" for item in issues),
        issues=issues,
        checked_tasks=len({record.task.task_id for record in records}),
    )
