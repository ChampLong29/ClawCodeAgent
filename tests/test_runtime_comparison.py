from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from claw.benchmark import BenchmarkError, RuntimeComparisonReportGenerator
from claw.main import main


def _run(runtime: str, *, success_rate: float, model: str = "provider/model"):
    return {
        "schema_version": "benchmark_run.v1",
        "run_id": f"run-{runtime}",
        "config": {
            "group_name": "base",
            "model_ref": model,
            "test_manifest_ref": "task_suites/manifest.json",
            "decoding_config": {
                "temperature": 0.0,
                "temperature_is_explicit": True,
                "max_tokens": 4096,
                "max_turns": 12,
                **(
                    {"sandbox_attestation": "test-sandbox:isolated"}
                    if runtime == "pi"
                    else {}
                ),
            },
            "tool_schema_version": f"{runtime}-tools.v1",
            "runtime_version": f"{runtime}@1.0.0",
            "verifier_bundle_version": "verifier-policy.v1",
            "prompt_version": f"{runtime}-prompt.v1",
            "seed": 42,
        },
        "task_ids": ["task-1"],
        "metrics": {
            "sample_count": 1,
            "task_success_rate": success_rate,
            "test_pass_rate": success_rate,
            "tool_selection_validity": 1.0,
            "tool_argument_validity": 1.0,
            "schema_format_validity": 1.0,
            "process_violation_rate": 0.0,
            "average_turns": 4.0 if runtime == "claw" else 5.0,
            "average_tokens": 100.0 if runtime == "claw" else 120.0,
            "average_latency_seconds": 2.0 if runtime == "claw" else 3.0,
        },
        "episodes": [
            {
                "task_id": "task-1",
                "success": bool(success_rate),
                "test_pass_rate": success_rate,
                "tool_calls": 2,
                "turns": 4 if runtime == "claw" else 5,
                "input_tokens": 80,
                "output_tokens": 20 if runtime == "claw" else 40,
                "latency_seconds": 2.0 if runtime == "claw" else 3.0,
                "bad_cases": [] if success_rate else ["test_failure"],
            }
        ],
    }


class RuntimeComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_generates_guarded_claw_pi_comparison(self):
        report = RuntimeComparisonReportGenerator(self.root).generate(
            _run("claw", success_rate=1.0),
            _run("pi", success_rate=0.0),
        )

        self.assertTrue(report["comparability"]["comparable"])
        self.assertEqual(report["metric_deltas_pi_minus_claw"]["task_success_rate"], -1.0)
        self.assertEqual(report["tasks"][0]["task_id"], "task-1")
        self.assertEqual(report["tasks"][0]["claw"]["success"], True)
        self.assertEqual(report["tasks"][0]["pi"]["success"], False)
        self.assertTrue((self.root / "runtime-comparison.json").is_file())
        self.assertTrue((self.root / "report.md").is_file())

    def test_model_mismatch_is_reported_as_non_comparable(self):
        report = RuntimeComparisonReportGenerator(self.root).generate(
            _run("claw", success_rate=1.0),
            _run("pi", success_rate=1.0, model="provider/other"),
        )

        self.assertFalse(report["comparability"]["comparable"])
        self.assertIn("model_ref", report["comparability"]["mismatches"])

    def test_decoding_mismatch_is_reported_as_non_comparable(self):
        pi = _run("pi", success_rate=1.0)
        pi["config"]["decoding_config"]["max_tokens"] = 8192
        report = RuntimeComparisonReportGenerator(self.root).generate(
            _run("claw", success_rate=1.0),
            pi,
        )

        self.assertFalse(report["comparability"]["comparable"])
        self.assertIn("max_tokens", report["comparability"]["mismatches"])

    def test_different_task_order_is_rejected(self):
        pi = _run("pi", success_rate=1.0)
        pi["task_ids"] = ["task-2"]
        pi["episodes"][0]["task_id"] = "task-2"
        with self.assertRaisesRegex(BenchmarkError, "same ordered task set"):
            RuntimeComparisonReportGenerator(self.root).generate(
                _run("claw", success_rate=1.0),
                pi,
            )

    def test_cli_loads_runs_and_prints_report_reference(self):
        claw_path = self.root / "claw.json"
        pi_path = self.root / "pi.json"
        claw_path.write_text(json.dumps(_run("claw", success_rate=1.0)))
        pi_path.write_text(json.dumps(_run("pi", success_rate=1.0)))
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main([
                "benchmark-compare",
                "--claw-run", str(claw_path),
                "--pi-run", str(pi_path),
                "--output", str(self.root / "comparison"),
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(payload["comparable"])
        self.assertTrue(Path(payload["comparison_ref"]).is_file())


if __name__ == "__main__":
    unittest.main()
