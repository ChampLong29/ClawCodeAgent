"""Versioned executable task-suite manifests and split invariants."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from ..episode.checkpoint import workspace_hash
from ..experiment.schemas import (
    TaskSpec,
    SchemaValidationError,
    canonical_hash,
)


TASK_SUITE_SCHEMA_VERSION = "task_suite.v1"


@dataclass
class TaskSuiteManifest:
    suite_id: str
    version: str
    tasks: List[TaskSpec]
    content_hash: str
    description: str = ""
    generated_by: str = ""
    validation: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = TASK_SUITE_SCHEMA_VERSION
    source_path: Optional[Path] = None

    @classmethod
    def load(
        cls, path: Union[str, Path], *, verify_templates: bool = True
    ) -> "TaskSuiteManifest":
        source = Path(path).resolve()
        with source.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        manifest = cls(
            suite_id=str(data["suite_id"]),
            version=str(data["version"]),
            description=str(data.get("description", "")),
            generated_by=str(data.get("generated_by", "")),
            validation=dict(data.get("validation", {})),
            tasks=[TaskSpec.from_dict(item) for item in data.get("tasks", [])],
            content_hash=str(data["content_hash"]),
            schema_version=str(
                data.get("schema_version", TASK_SUITE_SCHEMA_VERSION)
            ),
            source_path=source,
        )
        manifest.validate(verify_templates=verify_templates)
        return manifest

    @property
    def project_root(self) -> Path:
        if self.source_path is None:
            raise SchemaValidationError(
                "source_path is required to resolve task assets"
            )
        current = self.source_path.parent
        while current.parent != current:
            if (current / "pyproject.toml").is_file():
                return current
            current = current.parent
        raise SchemaValidationError(
            f"cannot find project root above {self.source_path}"
        )

    def resolve_ref(self, reference: str) -> Path:
        path = Path(reference)
        if not path.is_absolute():
            path = self.project_root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise SchemaValidationError(
                f"task asset escapes project root: {reference}"
            ) from exc
        return resolved

    def compute_content_hash(self) -> str:
        payload = {
                "suite_id": self.suite_id,
                "version": self.version,
                "description": self.description,
                "generated_by": self.generated_by,
                "tasks": [task.to_dict() for task in self.tasks],
            }
        if self.validation:
            payload["validation"] = self.validation
        return canonical_hash(payload)

    def validate(
        self,
        *,
        verify_templates: bool = True,
        min_tasks: Optional[int] = None,
        min_domains: Optional[int] = None,
        min_task_types: Optional[int] = None,
    ) -> None:
        policy = self.validation or {}
        min_tasks = int(policy.get("min_tasks", 30) if min_tasks is None else min_tasks)
        min_domains = int(policy.get("min_domains", 2) if min_domains is None else min_domains)
        min_task_types = int(
            policy.get("min_task_types", 2)
            if min_task_types is None
            else min_task_types
        )
        required_splits = policy.get(
            "required_splits", ["train", "dev", "test"]
        )
        if not isinstance(required_splits, list) or not required_splits:
            raise SchemaValidationError(
                "validation.required_splits must be a non-empty list"
            )
        if not set(required_splits).issubset({"train", "dev", "test"}):
            raise SchemaValidationError(
                "validation.required_splits contains an unsupported split"
            )
        if self.schema_version != TASK_SUITE_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported task suite schema: {self.schema_version}"
            )
        if not self.suite_id.strip() or not self.version.strip():
            raise SchemaValidationError(
                "suite_id and version must not be empty"
            )
        if len(self.tasks) < min_tasks:
            raise SchemaValidationError(
                f"task suite requires at least {min_tasks} tasks"
            )
        task_ids = [task.task_id for task in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise SchemaValidationError("task IDs must be unique")
        for task in self.tasks:
            task.validate()
        domains = {task.domain for task in self.tasks}
        task_types = {task.task_type for task in self.tasks}
        if len(domains) < min_domains:
            raise SchemaValidationError(
                f"task suite requires at least {min_domains} domains"
            )
        if len(task_types) < min_task_types:
            raise SchemaValidationError(
                f"task suite requires at least {min_task_types} task types"
            )
        family_splits: Dict[str, set] = defaultdict(set)
        for task in self.tasks:
            family_splits[task.family_id].add(task.split)
        leaking = sorted(
            family_id
            for family_id, splits in family_splits.items()
            if len(splits) > 1
        )
        if leaking:
            raise SchemaValidationError(
                f"task families cross splits: {leaking}"
            )
        for split in required_splits:
            if not any(task.split == split for task in self.tasks):
                raise SchemaValidationError(
                    f"task suite has no {split} tasks"
                )
        expected_hash = self.compute_content_hash()
        if self.content_hash != expected_hash:
            raise SchemaValidationError(
                f"suite content_hash mismatch: expected {expected_hash}, "
                f"got {self.content_hash}"
            )
        if verify_templates:
            self.verify_asset_hashes()

    def verify_asset_hashes(self) -> None:
        for task in self.tasks:
            template = self.resolve_ref(task.template_ref)
            oracle = (
                self.resolve_ref(task.oracle_ref) if task.oracle_ref else None
            )
            test_assets = (
                self.resolve_ref(task.test_assets_ref)
                if task.test_assets_ref
                else None
            )
            if not template.is_dir():
                raise SchemaValidationError(
                    f"template is not a directory: {task.template_ref}"
                )
            if oracle is None or not oracle.is_dir():
                raise SchemaValidationError(
                    f"oracle is not a directory: {task.oracle_ref}"
                )
            observed = workspace_hash(template, normalize_exec=True)
            if observed != task.template_hash:
                raise SchemaValidationError(
                    f"template hash mismatch for {task.task_id}: "
                    f"expected {task.template_hash}, got {observed}"
                )
            if test_assets is not None:
                if not test_assets.is_dir():
                    raise SchemaValidationError(
                        f"test assets are not a directory: {task.test_assets_ref}"
                    )
                observed_tests = workspace_hash(test_assets, normalize_exec=True)
                if observed_tests != task.test_assets_hash:
                    raise SchemaValidationError(
                        f"test assets hash mismatch for {task.task_id}: "
                        f"expected {task.test_assets_hash}, got {observed_tests}"
                    )

    def tasks_for_split(self, split: str) -> List[TaskSpec]:
        if split not in {"train", "dev", "test"}:
            raise ValueError("split must be train, dev, or test")
        return [task for task in self.tasks if task.split == split]

    def get(self, task_id: str) -> TaskSpec:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def summary(self) -> Dict[str, Any]:
        return {
            "task_count": len(self.tasks),
            "by_split": dict(
                sorted(Counter(task.split for task in self.tasks).items())
            ),
            "by_domain": dict(
                sorted(Counter(task.domain for task in self.tasks).items())
            ),
            "by_task_type": dict(
                sorted(Counter(task.task_type for task in self.tasks).items())
            ),
            "by_difficulty": dict(
                sorted(Counter(task.difficulty for task in self.tasks).items())
            ),
            "family_count": len({task.family_id for task in self.tasks}),
        }

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "version": self.version,
            "description": self.description,
            "generated_by": self.generated_by,
            "content_hash": self.content_hash,
            "tasks": [task.to_dict() for task in self.tasks],
        }
        if self.validation:
            data["validation"] = self.validation
        return data


def ensure_family_split_isolation(tasks: Iterable[TaskSpec]) -> None:
    """Reusable family-level leakage guard for arbitrary task selections."""
    family_splits: Dict[str, set] = defaultdict(set)
    for task in tasks:
        family_splits[task.family_id].add(task.split)
    leaking = sorted(
        family_id
        for family_id, splits in family_splits.items()
        if len(splits) > 1
    )
    if leaking:
        raise SchemaValidationError(f"task families cross splits: {leaking}")
