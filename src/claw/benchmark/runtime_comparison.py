"""Guarded end-to-end comparison reports for Claw and Pi benchmark runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Union

from ..experiment.schemas import canonical_hash
from ..training_backends.base import atomic_write_text
from .runner import BenchmarkError


COMPARISON_SCHEMA_VERSION = "runtime_comparison.v1"
_METRICS = (
    "task_success_rate",
    "test_pass_rate",
    "tool_selection_validity",
    "tool_argument_validity",
    "schema_format_validity",
    "process_violation_rate",
    "average_turns",
    "average_tokens",
    "average_latency_seconds",
)


def load_benchmark_run(path: Union[str, Path]) -> Dict[str, Any]:
    source = Path(path).resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"unable to load benchmark run {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkError(f"benchmark run must be a JSON object: {source}")
    return data


class RuntimeComparisonReportGenerator:
    """Compare two archived runs while making control mismatches explicit."""

    def __init__(self, output_dir: Union[str, Path]):
        self.output_dir = Path(output_dir).resolve()

    def generate(
        self,
        claw_run: Mapping[str, Any],
        pi_run: Mapping[str, Any],
    ) -> Dict[str, Any]:
        claw = self._validate_run("claw", claw_run)
        pi = self._validate_run("pi", pi_run)
        if claw["task_ids"] != pi["task_ids"]:
            raise BenchmarkError(
                "Claw and Pi runs must use the same ordered task set"
            )

        mismatches = self._control_mismatches(claw, pi)
        pi_decoding = pi["config"].get("decoding_config", {})
        sandbox_attestation = (
            pi_decoding.get("sandbox_attestation")
            if isinstance(pi_decoding, Mapping)
            else None
        )
        if not str(sandbox_attestation or "").strip():
            mismatches["pi_sandbox_attestation"] = {
                "claw": "not-applicable",
                "pi": sandbox_attestation,
            }

        metric_deltas = self._metric_deltas(claw["metrics"], pi["metrics"])
        report = {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "report_id": "runtime_comparison_" + canonical_hash(
                {
                    "claw_run_id": claw["run_id"],
                    "pi_run_id": pi["run_id"],
                    "mismatches": mismatches,
                }
            )[:20],
            "comparison_scope": "end_to_end_runtime",
            "claw_run_id": claw["run_id"],
            "pi_run_id": pi["run_id"],
            "task_ids": list(claw["task_ids"]),
            "comparability": {
                "comparable": not mismatches,
                "mismatches": mismatches,
                "controlled_fields": [
                    "test_manifest_ref",
                    "model_ref",
                    "verifier_bundle_version",
                    "seed",
                    "temperature",
                    "temperature_is_explicit",
                    "max_tokens",
                    "max_turns",
                    "ordered_task_set",
                    "pi_sandbox_attestation",
                ],
            },
            "runtime_dimensions": {
                "claw": self._runtime_dimensions(claw),
                "pi": self._runtime_dimensions(pi),
            },
            "metrics": {
                "claw": dict(claw["metrics"]),
                "pi": dict(pi["metrics"]),
            },
            "metric_deltas_pi_minus_claw": metric_deltas,
            "tasks": self._task_rows(claw, pi),
            "limitations": [
                "Runtime, system prompt, and tool schema are treatment variables; "
                "this is an end-to-end runtime comparison, not an isolated agent-loop ablation.",
                "A small task count is descriptive evidence only and does not establish statistical significance.",
                "The Pi sandbox attestation is operator-supplied provenance, not independently verified by this report.",
                "Pi RPC has no temperature command. For anthropic-messages, a recorded temperature is not an explicit Pi request control; the report marks that mismatch.",
            ],
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.output_dir / "runtime-comparison.json",
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            self.output_dir / "report.md",
            self._markdown(report),
        )
        return report

    @staticmethod
    def _validate_run(label: str, run: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(run, Mapping):
            raise BenchmarkError(f"{label} benchmark run must be an object")
        data = dict(run)
        if data.get("schema_version") != "benchmark_run.v1":
            raise BenchmarkError(f"{label} run has unsupported benchmark schema")
        for name in ("run_id", "config", "task_ids", "metrics", "episodes"):
            if name not in data:
                raise BenchmarkError(f"{label} run is missing {name}")
        if not isinstance(data["config"], Mapping):
            raise BenchmarkError(f"{label} run config must be an object")
        if not isinstance(data["task_ids"], list) or not data["task_ids"]:
            raise BenchmarkError(f"{label} run task_ids must be a non-empty list")
        if len(set(data["task_ids"])) != len(data["task_ids"]):
            raise BenchmarkError(f"{label} run task_ids must be unique")
        if not isinstance(data["metrics"], Mapping):
            raise BenchmarkError(f"{label} run metrics must be an object")
        if not isinstance(data["episodes"], list):
            raise BenchmarkError(f"{label} run episodes must be a list")
        episode_ids = [item.get("task_id") for item in data["episodes"]]
        if episode_ids != data["task_ids"]:
            raise BenchmarkError(f"{label} run episodes do not match task_ids")
        data["config"] = dict(data["config"])
        data["metrics"] = dict(data["metrics"])
        return data

    @staticmethod
    def _control_mismatches(
        claw: Mapping[str, Any],
        pi: Mapping[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        claw_config = claw["config"]
        pi_config = pi["config"]
        fields = (
            "test_manifest_ref",
            "model_ref",
            "verifier_bundle_version",
            "seed",
        )
        mismatches: Dict[str, Dict[str, Any]] = {}
        for field in fields:
            if claw_config.get(field) != pi_config.get(field):
                mismatches[field] = {
                    "claw": claw_config.get(field),
                    "pi": pi_config.get(field),
                }
        claw_decoding = claw_config.get("decoding_config", {})
        pi_decoding = pi_config.get("decoding_config", {})
        for field in ("temperature", "max_tokens", "max_turns"):
            claw_value = (
                claw_decoding.get(field)
                if isinstance(claw_decoding, Mapping)
                else None
            )
            pi_value = (
                pi_decoding.get(field)
                if isinstance(pi_decoding, Mapping)
                else None
            )
            if claw_value != pi_value:
                mismatches[field] = {"claw": claw_value, "pi": pi_value}
        claw_temperature_explicit = (
            claw_decoding.get("temperature_is_explicit", True)
            if isinstance(claw_decoding, Mapping)
            else True
        )
        pi_temperature_explicit = (
            pi_decoding.get("temperature_is_explicit")
            if isinstance(pi_decoding, Mapping)
            else None
        )
        if pi_temperature_explicit is None:
            pi_temperature_explicit = not str(
                pi_config.get("runtime_version", "")
            ).startswith("pi@")
        if claw_temperature_explicit != pi_temperature_explicit:
            mismatches["temperature_is_explicit"] = {
                "claw": claw_temperature_explicit,
                "pi": pi_temperature_explicit,
            }
        return mismatches

    @staticmethod
    def _runtime_dimensions(run: Mapping[str, Any]) -> Dict[str, Any]:
        config = run["config"]
        return {
            "runtime_version": config.get("runtime_version"),
            "prompt_version": config.get("prompt_version"),
            "tool_schema_version": config.get("tool_schema_version"),
            "decoding_config": config.get("decoding_config", {}),
        }

    @staticmethod
    def _metric_deltas(
        claw_metrics: Mapping[str, Any],
        pi_metrics: Mapping[str, Any],
    ) -> Dict[str, Any]:
        deltas: Dict[str, Any] = {}
        for name in _METRICS:
            claw_value = claw_metrics.get(name)
            pi_value = pi_metrics.get(name)
            if (
                isinstance(claw_value, (int, float))
                and not isinstance(claw_value, bool)
                and isinstance(pi_value, (int, float))
                and not isinstance(pi_value, bool)
            ):
                deltas[name] = pi_value - claw_value
            else:
                deltas[name] = None
        return deltas

    @staticmethod
    def _task_rows(
        claw: Mapping[str, Any],
        pi: Mapping[str, Any],
    ) -> Sequence[Dict[str, Any]]:
        def summary(episode: Mapping[str, Any]) -> Dict[str, Any]:
            return {
                key: episode.get(key)
                for key in (
                    "success",
                    "test_pass_rate",
                    "tool_calls",
                    "turns",
                    "input_tokens",
                    "output_tokens",
                    "latency_seconds",
                    "bad_cases",
                    "error",
                    "trajectory_ref",
                    "verification_ref",
                )
            }

        return [
            {
                "task_id": task_id,
                "claw": summary(claw_episode),
                "pi": summary(pi_episode),
            }
            for task_id, claw_episode, pi_episode in zip(
                claw["task_ids"], claw["episodes"], pi["episodes"]
            )
        ]

    @staticmethod
    def _markdown(report: Mapping[str, Any]) -> str:
        status = (
            "comparable"
            if report["comparability"]["comparable"]
            else "not comparable"
        )
        lines = [
            "# Claw–Pi runtime comparison",
            "",
            f"- Report ID: `{report['report_id']}`",
            f"- Status: **{status}**",
            f"- Tasks: {len(report['task_ids'])}",
            "",
            "| Metric | Claw | Pi | Pi − Claw |",
            "|---|---:|---:|---:|",
        ]
        for metric in _METRICS:
            claw_value = report["metrics"]["claw"].get(metric)
            pi_value = report["metrics"]["pi"].get(metric)
            delta = report["metric_deltas_pi_minus_claw"].get(metric)
            lines.append(f"| {metric} | {claw_value} | {pi_value} | {delta} |")
        mismatches = report["comparability"]["mismatches"]
        lines.extend(["", "## Comparability", ""])
        if mismatches:
            lines.extend(
                f"- `{name}`: Claw=`{values['claw']}`, Pi=`{values['pi']}`"
                for name, values in sorted(mismatches.items())
            )
        else:
            lines.append("- Required controls match.")
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in report["limitations"])
        return "\n".join(lines) + "\n"
