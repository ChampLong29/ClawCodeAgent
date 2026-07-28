"""Atomic experiment registry and UI-independent evidence reports."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .schemas import (
    EXPERIMENT_STATUSES,
    ExperimentRun,
    SchemaValidationError,
    canonical_hash,
    utc_now,
)


EXPERIMENT_REPORT_SCHEMA_VERSION = "experiment_report.v1"

_TRANSITIONS = {
    "created": {
        "training",
        "trained",
        "contract_verified",
        "benchmarking",
        "completed",
        "failed",
    },
    "training": {
        "trained",
        "contract_verified",
        "failed",
        "recovery_required",
    },
    "trained": {"benchmarking", "completed", "failed"},
    "contract_verified": {"training", "failed"},
    "benchmarking": {"completed", "failed", "recovery_required"},
    "completed": set(),
    "failed": {"training", "benchmarking"},
    "recovery_required": {"training", "benchmarking", "failed"},
}


class ExperimentRegistryError(RuntimeError):
    """Raised when experiment lineage or state transitions are invalid."""


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _append_unique(values: List[str], additions: List[str]) -> List[str]:
    return list(dict.fromkeys([*values, *[item for item in additions if item]]))


class ExperimentRegistry:
    """Persist one authoritative, fully reconstructable record per experiment."""

    def __init__(self, root: Union[str, os.PathLike[str]]):
        self.root = Path(root).resolve()

    def _run_dir(self, experiment_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", experiment_id):
            raise ExperimentRegistryError("unsafe experiment_id")
        return self.root / experiment_id

    def _run_path(self, experiment_id: str) -> Path:
        return self._run_dir(experiment_id) / "experiment-run.json"

    def _write(self, run: ExperimentRun) -> Path:
        run.validate()
        destination = self._run_path(run.experiment_id)
        _atomic_write_text(
            destination,
            json.dumps(
                run.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
        )
        return destination

    def create(self, run: ExperimentRun) -> Path:
        run.validate()
        if run.status != "created":
            raise ExperimentRegistryError(
                "new experiments must start in the created state"
            )
        if not run.git_commit.strip():
            raise ExperimentRegistryError(
                "registered experiments require an immutable git_commit"
            )
        if not run.environment:
            raise ExperimentRegistryError(
                "registered experiments require an environment snapshot"
            )
        destination = self._run_path(run.experiment_id)
        if destination.exists():
            raise FileExistsError(f"experiment already exists: {run.experiment_id}")
        if not run.status_history:
            run.status_history.append(
                {
                    "status": run.status,
                    "timestamp": run.created_at,
                    "detail": "experiment registered",
                }
            )
        run.updated_at = utc_now()
        return self._write(run)

    def load(self, experiment_id: str) -> ExperimentRun:
        path = self._run_path(experiment_id)
        if not path.is_file():
            raise FileNotFoundError(f"experiment not found: {experiment_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExperimentRegistryError(
                f"invalid experiment record: {path}"
            ) from exc
        return ExperimentRun.from_dict(payload)

    def list_runs(self) -> List[ExperimentRun]:
        if not self.root.is_dir():
            return []
        runs: List[ExperimentRun] = []
        for path in sorted(self.root.glob("*/experiment-run.json")):
            runs.append(self.load(path.parent.name))
        return runs

    def _set_status(
        self, run: ExperimentRun, target: str, detail: str = ""
    ) -> None:
        if target not in EXPERIMENT_STATUSES:
            raise ExperimentRegistryError(f"unsupported target status: {target}")
        if target == run.status:
            return
        if target not in _TRANSITIONS[run.status]:
            raise ExperimentRegistryError(
                f"illegal experiment transition: {run.status} -> {target}"
            )
        run.status = target
        run.updated_at = utc_now()
        run.status_history.append(
            {"status": target, "timestamp": run.updated_at, "detail": detail}
        )

    def transition(
        self, experiment_id: str, target: str, detail: str = ""
    ) -> ExperimentRun:
        run = self.load(experiment_id)
        self._set_status(run, target, detail)
        self._write(run)
        return run

    def recover_interrupted(self) -> List[str]:
        recovered: List[str] = []
        for run in self.list_runs():
            if run.status not in {"training", "benchmarking"}:
                continue
            self._set_status(
                run,
                "recovery_required",
                f"interrupted while {run.status}",
            )
            self._write(run)
            recovered.append(run.experiment_id)
        return recovered

    def attach_training(
        self,
        experiment_id: str,
        record: Any,
        record_ref: str,
    ) -> ExperimentRun:
        record.validate()
        run = self.load(experiment_id)
        snapshot = record.to_dict()
        if run.training_run_ref and run.training_run_ref != str(record_ref):
            raise ExperimentRegistryError(
                "training evidence is already attached under another ref"
            )
        existing_training = run.evidence.get("training")
        if (
            existing_training
            and run.status in {"trained", "contract_verified", "completed"}
        ):
            if (
                run.training_run_ref == str(record_ref)
                and existing_training == snapshot
            ):
                return run
            raise ExperimentRegistryError(
                "finalized training evidence cannot be replaced"
            )
        if record.experiment_id != run.experiment_id:
            raise ExperimentRegistryError("training experiment_id mismatch")
        if record.base_model_ref != run.base_model_ref:
            raise ExperimentRegistryError("training base model mismatch")
        if record.dataset_id != run.dataset_manifest_ref:
            raise ExperimentRegistryError("training dataset mismatch")
        if record.backend != run.training_backend:
            raise ExperimentRegistryError("training backend mismatch")
        if record.seed != run.seed:
            raise ExperimentRegistryError("training seed mismatch")
        if record.training_config != run.training_config:
            raise ExperimentRegistryError("training configuration mismatch")
        if not str(record_ref).strip():
            raise ExperimentRegistryError("training record_ref must not be empty")

        run.training_run_ref = str(record_ref)
        run.adapter_ref = record.adapter_ref
        run.evidence["training"] = snapshot
        run.metrics["training"] = dict(record.metrics)
        run.artifact_refs = _append_unique(
            run.artifact_refs,
            [str(record_ref), *list(record.artifact_refs)],
        )
        if record.status in {"created", "prepared", "running"}:
            target = "training"
        elif record.status == "failed":
            target = "failed"
        elif record.training_verified:
            target = "trained"
        else:
            target = "contract_verified"
        self._set_status(
            run,
            target,
            "attached real training evidence"
            if record.training_verified
            else "attached non-training contract evidence",
        )
        self._write(run)
        return run

    def attach_benchmark(
        self,
        experiment_id: str,
        result: Any,
        result_ref: str,
    ) -> ExperimentRun:
        result.validate()
        run = self.load(experiment_id)
        snapshot = result.to_dict()
        if run.benchmark_ref:
            if (
                run.benchmark_ref == str(result_ref)
                and run.evidence.get("benchmark") == snapshot
            ):
                return run
            raise ExperimentRegistryError(
                "finalized benchmark evidence cannot be replaced"
            )
        config = result.config
        if config.experiment_ref and config.experiment_ref != run.experiment_id:
            raise ExperimentRegistryError("benchmark experiment_ref mismatch")
        if config.seed != run.seed:
            raise ExperimentRegistryError("benchmark seed mismatch")
        if config.group_name != "base":
            if run.status != "trained":
                raise ExperimentRegistryError(
                    "trained benchmark requires verified training evidence"
                )
            if config.dataset_manifest_ref != run.dataset_manifest_ref:
                raise ExperimentRegistryError("benchmark dataset mismatch")
            if config.training_run_ref != run.training_run_ref:
                raise ExperimentRegistryError("benchmark training run mismatch")
            if run.adapter_ref and config.model_ref != run.adapter_ref:
                raise ExperimentRegistryError("benchmark adapter mismatch")
        elif config.model_ref != run.base_model_ref:
            raise ExperimentRegistryError("base benchmark model mismatch")
        if not str(result_ref).strip():
            raise ExperimentRegistryError("benchmark result_ref must not be empty")

        run.benchmark_ref = str(result_ref)
        run.inference_config = dict(config.decoding_config)
        run.evidence["benchmark"] = snapshot
        run.metrics["benchmark"] = dict(result.metrics)
        run.result_refs = _append_unique(
            run.result_refs,
            [str(result_ref), *list(result.output_refs)],
        )
        run.artifact_refs = _append_unique(
            run.artifact_refs,
            [str(result_ref), *list(result.output_refs)],
        )
        self._set_status(run, "completed", "attached benchmark evidence")
        self._write(run)
        return run

    def attach_ablation(
        self,
        experiment_id: str,
        report: Dict[str, Any],
        report_ref: str,
    ) -> ExperimentRun:
        run = self.load(experiment_id)
        if report.get("schema_version") != "ablation_report.v1":
            raise ExperimentRegistryError("unsupported ablation report")
        if not str(report_ref).strip():
            raise ExperimentRegistryError("ablation report_ref must not be empty")
        run.evidence["ablation"] = dict(report)
        run.result_refs = _append_unique(run.result_refs, [str(report_ref)])
        run.artifact_refs = _append_unique(run.artifact_refs, [str(report_ref)])
        run.updated_at = utc_now()
        self._write(run)
        return run

    def build_report(self, experiment_id: str) -> Dict[str, Any]:
        run = self.load(experiment_id)
        payload: Dict[str, Any] = {
            "schema_version": EXPERIMENT_REPORT_SCHEMA_VERSION,
            "run": run.to_dict(),
        }
        payload["content_hash"] = canonical_hash(payload)
        return payload

    def render_report(self, experiment_id: str) -> Dict[str, str]:
        report = self.build_report(experiment_id)
        run = report["run"]
        output_dir = self._run_dir(experiment_id)
        json_path = output_dir / "experiment-report.json"
        markdown_path = output_dir / "experiment-report.md"
        _atomic_write_text(
            json_path,
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        _atomic_write_text(markdown_path, self._render_markdown(report))
        return {"json": str(json_path), "markdown": str(markdown_path)}

    @staticmethod
    def verify_report(
        source: Union[str, os.PathLike[str], Dict[str, Any]]
    ) -> bool:
        if isinstance(source, dict):
            payload = dict(source)
        else:
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
        expected = payload.pop("content_hash", "")
        return bool(expected) and expected == canonical_hash(payload)

    @staticmethod
    def _render_markdown(report: Dict[str, Any]) -> str:
        run = report["run"]
        training = run.get("evidence", {}).get("training")
        benchmark = run.get("evidence", {}).get("benchmark")
        training_truth = (
            "verified real PeFT training"
            if training and training.get("training_verified")
            else "not verified as real training"
        )
        history = "\n".join(
            f"- `{item['timestamp']}` — **{item['status']}**"
            + (f": {item.get('detail', '')}" if item.get("detail") else "")
            for item in run.get("status_history", [])
        ) or "- No status events recorded."
        return (
            f"# Experiment {run['experiment_id']}\n\n"
            f"Status: **{run['status']}**  \n"
            f"Hypothesis: {run['hypothesis']}  \n"
            f"Git commit: `{run.get('git_commit', '')}`  \n"
            f"Base model: `{run['base_model_ref']}`  \n"
            f"Dataset: `{run['dataset_manifest_ref']}`  \n"
            f"Training backend: `{run['training_backend']}`  \n"
            f"Training evidence: **{training_truth}**  \n"
            f"Training run: `{run.get('training_run_ref') or ''}`  \n"
            f"Adapter: `{run.get('adapter_ref') or ''}`  \n"
            f"Benchmark: `{run.get('benchmark_ref') or ''}`  \n"
            f"Report content hash: `{report['content_hash']}`\n\n"
            "## Status history\n\n"
            f"{history}\n\n"
            "## Environment\n\n"
            "```json\n"
            f"{json.dumps(run.get('environment', {}), ensure_ascii=False, indent=2, sort_keys=True)}\n"
            "```\n\n"
            "## Training configuration\n\n"
            "```json\n"
            f"{json.dumps(run.get('training_config', {}), ensure_ascii=False, indent=2, sort_keys=True)}\n"
            "```\n\n"
            "## Inference configuration\n\n"
            "```json\n"
            f"{json.dumps(run.get('inference_config', {}), ensure_ascii=False, indent=2, sort_keys=True)}\n"
            "```\n\n"
            "## Metrics\n\n"
            "```json\n"
            f"{json.dumps(run.get('metrics', {}), ensure_ascii=False, indent=2, sort_keys=True)}\n"
            "```\n\n"
            "## Evidence\n\n"
            "```json\n"
            f"{json.dumps(run.get('evidence', {}), ensure_ascii=False, indent=2, sort_keys=True)}\n"
            "```\n\n"
            "## Artifact references\n\n"
            + (
                "\n".join(f"- `{ref}`" for ref in run.get("artifact_refs", []))
                or "- None."
            )
            + "\n"
        )
