"""Generate five deterministic, sanitized Silver records for integration tests."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claw.data_pipeline import SilverDatasetBuilder
from claw.dataset import DatasetRecord
from claw.experiment.schemas import TaskSpec, VerificationReport, VerificationSignal
from claw.trajectory import rollout_result_to_trajectory


PROFILES = (
    ("fixture-python-cli-easy", "python-cli", "easy", True),
    ("fixture-python-cli-medium", "python-cli", "medium", True),
    ("fixture-python-library-medium", "python-library", "medium", True),
    ("fixture-python-service-hard-failure", "python-service", "hard", False),
    ("fixture-python-library-easy-failure", "python-library", "easy", False),
)


def _record(task_id: str, domain: str, difficulty: str, passed: bool) -> DatasetRecord:
    task = TaskSpec(
        task_id=task_id,
        task_version="1.0.0",
        family_id=f"family-{task_id}",
        domain=domain,
        task_type="fix_bug",
        difficulty=difficulty,
        split="train",
        prompt=f"Fix the deterministic fixture {task_id}",
        template_ref=f"fixtures/{task_id}",
        template_hash=(task_id.encode().hex() + "0" * 64)[:64],
        test_commands=["python -m unittest"],
        timeout_seconds=30,
        source="claw-sanitized-fixture",
        license="MIT",
        tags=["data-pipeline", "sanitized"],
    )
    task.content_hash = task.compute_content_hash()
    call_id = f"call-{task_id}"
    trajectory = rollout_result_to_trajectory(
        {
            "task_id": task_id,
            "session_id": f"session-{task_id}",
            "started_at": "2026-08-01T00:00:00+00:00",
            "stop_reason": "completed",
            "messages": [
                {"role": "system", "content": "You are a coding agent."},
                {"role": "user", "content": task.prompt},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "name": "write_file",
                            "arguments": {
                                "path": "src/fixture.py",
                                "content": "VALUE = 1\n",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": "written",
                },
                {"role": "assistant", "content": "Implemented and verified."},
            ],
            "test_result": {
                "passed_tests": 1 if passed else 0,
                "total_tests": 1,
            },
            "diff_result": {"changed_files": ["src/fixture.py"]},
            "usage": {
                "model_calls": 2,
                "tool_calls": 1,
                "input_tokens": 320,
                "output_tokens": 96,
            },
        },
        model_version="fixture-model.v1",
        runtime_version="fixture-runtime.v1",
        prompt_version="fixture-prompt.v1",
        tool_version="fixture-tools.v1",
        config_version="fixture-config.v1",
    )
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for event in trajectory.events:
        event.timestamp = (start + timedelta(seconds=event.seq)).isoformat()
    trajectory.header.started_at = start.isoformat()
    trajectory.header.finished_at = trajectory.events[-1].timestamp
    hard_status = "pass" if passed else "fail"
    signals = [
        VerificationSignal(
            name="test_pass_rate",
            kind="hard",
            status=hard_status,
            score=1.0 if passed else 0.0,
        ),
        *[
            VerificationSignal(name=name, kind="hard", status="pass", score=1.0)
            for name in (
                "build",
                "static_check",
                "diff_scope",
                "process_permission",
                "format_schema",
            )
        ],
        VerificationSignal(
            name="independent_reviewer",
            kind="soft",
            status="pass" if passed else "fail",
            score=0.92 if passed else 0.35,
            required=False,
        ),
    ]
    report = VerificationReport(
        report_id=f"verification-{task_id}",
        trajectory_ref=trajectory.header.trajectory_id,
        verifier_bundle_version="fixture-verifier.v1",
        verdict="success" if passed else "failure",
        hard_gate_passed=passed,
        signals=signals,
        aggregate_score=0.92 if passed else 0.35,
        bad_cases=([] if passed else [{"primary_category": "test_failure"}]),
        generated_at="2026-08-01T00:01:00+00:00",
    )
    return DatasetRecord(task=task, trajectory=trajectory, verification=report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", default="examples/data_pipeline/silver", type=Path
    )
    args = parser.parse_args()
    result = SilverDatasetBuilder(generation_commit="fixture-v1").build(
        [_record(*profile) for profile in PROFILES], output_dir=args.output_dir
    )
    print(result.manifest.dataset_id, result.manifest.record_count)


if __name__ == "__main__":
    main()
