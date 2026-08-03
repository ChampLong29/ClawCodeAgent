"""Executable proof that templates fail initially and references pass."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..experiment.schemas import TaskSpec, canonical_hash, utc_now
from .registry import TaskSuiteManifest


@dataclass
class CommandEvidence:
    command: str
    returncode: int
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass
class TaskValidationEvidence:
    task_id: str
    initial_failed: bool
    reference_passed: bool
    initial_results: List[CommandEvidence] = field(default_factory=list)
    reference_results: List[CommandEvidence] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.initial_failed and self.reference_passed and not self.error

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["passed"] = self.passed
        return data


@dataclass
class TaskSuiteValidationReport:
    suite_id: str
    suite_content_hash: str
    tasks: List[TaskValidationEvidence]
    generated_at: str = field(default_factory=utc_now)
    schema_version: str = "task_suite_validation.v1"

    @property
    def passed(self) -> bool:
        return bool(self.tasks) and all(item.passed for item in self.tasks)

    @property
    def report_id(self) -> str:
        return "task_validation_" + canonical_hash(
            {
                "suite_id": self.suite_id,
                "suite_content_hash": self.suite_content_hash,
                "tasks": [item.to_dict() for item in self.tasks],
            }
        )[:20]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "suite_id": self.suite_id,
            "suite_content_hash": self.suite_content_hash,
            "passed": self.passed,
            "task_count": len(self.tasks),
            "generated_at": self.generated_at,
            "tasks": [item.to_dict() for item in self.tasks],
        }


class TaskSuiteValidator:
    def __init__(self, manifest: TaskSuiteManifest):
        manifest.validate()
        self.manifest = manifest

    @staticmethod
    def _run(
        commands: List[str], workspace: Path, timeout: float
    ) -> List[CommandEvidence]:
        results = []
        for command in commands:
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                results.append(
                    CommandEvidence(
                        command=command,
                        returncode=completed.returncode,
                        duration_seconds=time.monotonic() - started,
                        stdout=completed.stdout[-4000:],
                        stderr=completed.stderr[-4000:],
                    )
                )
            except subprocess.TimeoutExpired as exc:
                results.append(
                    CommandEvidence(
                        command=command,
                        returncode=-1,
                        duration_seconds=time.monotonic() - started,
                        stdout=str(exc.stdout or "")[-4000:],
                        stderr=str(exc.stderr or "")[-4000:],
                        timed_out=True,
                    )
                )
        return results

    def validate_task(self, task: TaskSpec) -> TaskValidationEvidence:
        task.validate()
        template = self.manifest.resolve_ref(task.template_ref)
        oracle = self.manifest.resolve_ref(task.oracle_ref or "")
        with tempfile.TemporaryDirectory(prefix=f"claw_task_{task.task_id}_") as raw:
            workspace = Path(raw).resolve()
            try:
                shutil.copytree(template, workspace, dirs_exist_ok=True)
                initial = self._run_with_test_assets(
                    task, task.initial_checks, workspace
                )
                initial_failed = (
                    bool(initial)
                    and not any(item.timed_out for item in initial)
                    and any(item.returncode != 0 for item in initial)
                )
                shutil.copytree(oracle, workspace, dirs_exist_ok=True)
                reference = self._run_with_test_assets(
                    task, task.test_commands, workspace
                )
                reference_passed = bool(reference) and all(
                    item.returncode == 0 and not item.timed_out
                    for item in reference
                )
                return TaskValidationEvidence(
                    task_id=task.task_id,
                    initial_failed=initial_failed,
                    reference_passed=reference_passed,
                    initial_results=initial,
                    reference_results=reference,
                )
            except Exception as exc:
                return TaskValidationEvidence(
                    task_id=task.task_id,
                    initial_failed=False,
                    reference_passed=False,
                    error=f"{type(exc).__name__}: {exc}",
                )

    def _run_with_test_assets(
        self, task: TaskSpec, commands: List[str], workspace: Path
    ) -> List[CommandEvidence]:
        if not task.test_assets_ref:
            return self._run(commands, workspace, task.timeout_seconds)
        source = self.manifest.resolve_ref(task.test_assets_ref)
        destination = workspace / ".claw_hidden_tests"
        shutil.copytree(source, destination)
        try:
            return self._run(commands, workspace, task.timeout_seconds)
        finally:
            shutil.rmtree(destination, ignore_errors=True)

    def validate_all(self) -> TaskSuiteValidationReport:
        return TaskSuiteValidationReport(
            suite_id=self.manifest.suite_id,
            suite_content_hash=self.manifest.content_hash,
            tasks=[self.validate_task(task) for task in self.manifest.tasks],
        )

    @staticmethod
    def write_report(
        report: TaskSuiteValidationReport,
        path: Union[str, os.PathLike[str]],
    ) -> None:
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = (
            json.dumps(
                report.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
