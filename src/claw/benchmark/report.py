"""Four-group ablation validation and reproducible Markdown reporting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Union

from ..experiment.schemas import canonical_hash
from ..training_backends.base import atomic_write_text
from .runner import ABLATION_GROUPS, BenchmarkError, BenchmarkRunResult


_DISPLAY_NAMES = {
    "base": "Base",
    "raw_sft": "Raw-SFT",
    "success_sft": "Success-SFT",
    "verifier_sft": "Verifier-SFT",
}

_METRIC_COLUMNS = [
    ("task_success_rate", "Task success"),
    ("test_pass_rate", "Test pass"),
    ("tool_selection_validity", "Tool selection"),
    ("tool_argument_validity", "Tool arguments"),
    ("schema_format_validity", "Schema/format"),
    ("process_violation_rate", "Process violations"),
    ("average_turns", "Avg turns"),
    ("average_tokens", "Avg tokens"),
    ("average_latency_seconds", "Avg latency (s)"),
]


class AblationReportGenerator:
    """Require protocol-equivalent Base/Raw/Success/Verifier runs."""

    def __init__(self, output_dir: Union[str, Path]):
        self.output_dir = Path(output_dir).resolve()

    @staticmethod
    def validate_runs(runs: Sequence[BenchmarkRunResult]) -> None:
        by_group = {run.config.group_name: run for run in runs}
        if set(by_group) != ABLATION_GROUPS or len(runs) != len(ABLATION_GROUPS):
            raise BenchmarkError(
                "ablation requires exactly base, raw_sft, success_sft, "
                "and verifier_sft"
            )
        fingerprints = {
            run.config.protocol_fingerprint for run in runs
        }
        if len(fingerprints) != 1:
            raise BenchmarkError("ablation groups use different benchmark protocols")
        task_orders = {tuple(run.task_ids) for run in runs}
        if len(task_orders) != 1:
            raise BenchmarkError("ablation groups evaluated different task sets")
        for run in runs:
            run.validate()

    def generate(
        self, runs: Sequence[BenchmarkRunResult]
    ) -> Dict[str, Any]:
        self.validate_runs(runs)
        ordered = sorted(
            runs,
            key=lambda run: [
                "base",
                "raw_sft",
                "success_sft",
                "verifier_sft",
            ].index(run.config.group_name),
        )
        base = ordered[0]
        comparison = {}
        for run in ordered:
            group = run.config.group_name
            comparison[group] = {
                "display_name": _DISPLAY_NAMES[group],
                "run_id": run.run_id,
                "model_ref": run.config.model_ref,
                "dataset_manifest_ref": run.config.dataset_manifest_ref,
                "training_run_ref": run.config.training_run_ref,
                "experiment_ref": run.config.experiment_ref,
                "metrics": run.metrics,
                "task_success_delta_vs_base": (
                    run.metrics["task_success_rate"]
                    - base.metrics["task_success_rate"]
                ),
                "failed_task_ids": [
                    episode.task_id for episode in run.episodes if not episode.success
                ],
            }
        sample_count = len(base.task_ids)
        limitations = []
        if sample_count < 30:
            limitations.append(
                "The independent test set has fewer than 30 tasks; results are "
                "descriptive and no statistical significance is claimed."
            )
        report = {
            "schema_version": "ablation_report.v1",
            "report_id": "ablation_" + canonical_hash(
                {
                    "protocol": base.config.protocol_fingerprint,
                    "run_ids": [run.run_id for run in ordered],
                }
            )[:20],
            "protocol_fingerprint": base.config.protocol_fingerprint,
            "test_manifest_ref": base.config.test_manifest_ref,
            "task_ids": list(base.task_ids),
            "groups": comparison,
            "limitations": limitations,
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.output_dir / "ablation-report.json",
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            self.output_dir / "report.md", self._markdown(report, ordered)
        )
        return report

    @staticmethod
    def _format_metric(value: Any) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    def _markdown(
        self,
        report: Dict[str, Any],
        runs: Sequence[BenchmarkRunResult],
    ) -> str:
        headers = ["Group"] + [label for _, label in _METRIC_COLUMNS]
        lines: List[str] = [
            "# Independent Benchmark Ablation",
            "",
            f"- Report ID: `{report['report_id']}`",
            f"- Test manifest: `{report['test_manifest_ref']}`",
            f"- Protocol fingerprint: `{report['protocol_fingerprint']}`",
            f"- Tasks: {len(report['task_ids'])}",
            "",
            "| " + " | ".join(headers) + " |",
            "|" + "|".join(["---"] * len(headers)) + "|",
        ]
        for run in runs:
            values = [_DISPLAY_NAMES[run.config.group_name]]
            values.extend(
                self._format_metric(run.metrics.get(metric))
                for metric, _ in _METRIC_COLUMNS
            )
            lines.append("| " + " | ".join(values) + " |")
        lines.extend(["", "## Training lineage", ""])
        for run in runs:
            if run.config.group_name == "base":
                lines.append("- Base: no training dataset or adapter")
            else:
                lines.append(
                    f"- {_DISPLAY_NAMES[run.config.group_name]}: "
                    f"dataset=`{run.config.dataset_manifest_ref}`, "
                    f"training=`{run.config.training_run_ref}`, "
                    f"experiment=`{run.config.experiment_ref}`"
                )
        lines.extend(["", "## Failed samples", ""])
        for run in runs:
            failures = [
                episode.task_id for episode in run.episodes if not episode.success
            ]
            lines.append(
                f"- {_DISPLAY_NAMES[run.config.group_name]}: "
                + (", ".join(failures) if failures else "none")
            )
        lines.extend(["", "## Bad case distribution", ""])
        for run in runs:
            distribution = run.metrics.get("bad_case_distribution", {})
            rendered = ", ".join(
                f"{name}={count}" for name, count in sorted(distribution.items())
            )
            lines.append(
                f"- {_DISPLAY_NAMES[run.config.group_name]}: "
                + (rendered or "none")
            )
        if report["limitations"]:
            lines.extend(["", "## Limitations", ""])
            lines.extend(f"- {item}" for item in report["limitations"])
        return "\n".join(lines) + "\n"
