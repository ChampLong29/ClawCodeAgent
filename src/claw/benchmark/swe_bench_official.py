"""Audited boundary for invoking the official SWE-bench Docker harness.

This module deliberately lives on the evaluator side of the leakage boundary.
It exports one frozen raw dataset row plus one candidate patch in the official
prediction format, then invokes ``swebench.harness.run_evaluation`` without a
shell.  It does not generate patches and must never be called from an Agent
workspace.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


HARNESS_MODULE = "swebench.harness.run_evaluation"
RUNNER_SOURCE = '''from __future__ import annotations
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--dataset_name", required=True); parser.add_argument("--split", required=True)
parser.add_argument("--instance_ids", nargs="+", required=True); parser.add_argument("--predictions_path", required=True)
parser.add_argument("--max_workers", type=int, required=True); parser.add_argument("--run_id", required=True)
parser.add_argument("--timeout", type=int, required=True); parser.add_argument("--open_file_limit", type=int, required=True)
parser.add_argument("--report_dir", required=True); parser.add_argument("--rewrite_reports", required=True)
parser.add_argument("--task_repo", default=None)
args = parser.parse_args()
from swebench.harness.run_evaluation import main
main(dataset_name=args.dataset_name, split=args.split, instance_ids=args.instance_ids,
     predictions_path=args.predictions_path, max_workers=args.max_workers, run_id=args.run_id,
     timeout=args.timeout, open_file_limit=args.open_file_limit, report_dir=args.report_dir,
     rewrite_reports=args.rewrite_reports.lower() == "true", modal=False, task_repo=args.task_repo)
'''
OFFICIAL_PREDICTION_FIELDS = (
    "instance_id",
    "model_name_or_path",
    "model_patch",
)


class OfficialSweBenchHarnessError(RuntimeError):
    """Raised when the official evaluator contract is unavailable or invalid."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@dataclass(frozen=True)
class OfficialSweBenchHarnessConfig:
    python_executable: str
    expected_version: str = "5.0.2"
    docker_executable: str = "docker"
    max_workers: int = 1
    timeout_seconds: int = 1800
    open_file_limit: int = 4096

    def validate(self) -> None:
        if not self.python_executable.strip():
            raise ValueError("harness python executable must not be empty")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", self.expected_version):
            raise ValueError("expected SWE-bench version must be numeric and pinned")
        if (
            self.max_workers <= 0
            or self.timeout_seconds <= 0
            or self.open_file_limit <= 0
        ):
            raise ValueError("workers, timeout, and open-file limit must be positive")


@dataclass(frozen=True)
class PreparedOfficialSweBenchRun:
    run_id: str
    instance_id: str
    run_root: Path
    dataset_path: Path
    predictions_path: Path
    command: List[str]
    input_manifest_path: Path
    task_repo: Optional[Path] = None


class OfficialSweBenchHarness:
    """Prepare and run one selected instance with the official Docker harness."""

    def __init__(
        self,
        config: OfficialSweBenchHarnessConfig,
        *,
        executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        config.validate()
        executable = config.python_executable
        if "/" in executable or "\\" in executable:
            executable_path = Path(executable).expanduser()
            if not executable_path.is_absolute():
                executable_path = Path.cwd() / executable_path
            # Keep a virtual-environment launcher path intact. ``resolve()``
            # would follow ``bin/python`` to the system interpreter and lose
            # the environment's site-packages when the run changes cwd.
            executable_path = executable_path.absolute()
            config = replace(config, python_executable=str(executable_path))
        self.config = config
        self._executor = executor

    def _execute(
        self,
        args: Sequence[str],
        *,
        cwd: Optional[Path] = None,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        return self._executor(
            list(args),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    def preflight(self) -> Dict[str, Any]:
        """Verify the pinned package, current CLI contract, and Docker daemon."""
        metadata_script = (
            "import importlib.metadata as m, json; "
            "import swebench.harness.run_evaluation as h; "
            "print(json.dumps({'version': m.version('swebench'), "
            "'module': h.__file__}, sort_keys=True))"
        )
        package = self._execute(
            [self.config.python_executable, "-c", metadata_script]
        )
        if package.returncode != 0:
            raise OfficialSweBenchHarnessError(
                "official SWE-bench package is unavailable: "
                + (package.stderr.strip() or package.stdout.strip())
            )
        try:
            package_metadata = json.loads(package.stdout.strip().splitlines()[-1])
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise OfficialSweBenchHarnessError(
                "SWE-bench package metadata was not valid JSON"
            ) from exc
        if package_metadata.get("version") != self.config.expected_version:
            raise OfficialSweBenchHarnessError(
                "SWE-bench version mismatch: expected "
                f"{self.config.expected_version}, observed "
                f"{package_metadata.get('version')}"
            )

        help_result = self._execute(
            [self.config.python_executable, "-m", HARNESS_MODULE, "--help"]
        )
        required_options = {
            "--dataset_name",
            "--split",
            "--instance_ids",
            "--predictions_path",
            "--max_workers",
            "--run_id",
            "--timeout",
            "--open_file_limit",
            "--report_dir",
            "--rewrite_reports",
        }
        help_text = help_result.stdout + help_result.stderr
        missing_options = sorted(option for option in required_options if option not in help_text)
        if help_result.returncode != 0 or missing_options:
            raise OfficialSweBenchHarnessError(
                "official Harness CLI contract mismatch; missing options: "
                + ", ".join(missing_options)
            )

        docker = self._execute([self.config.docker_executable, "version"])
        if docker.returncode != 0:
            raise OfficialSweBenchHarnessError(
                "Docker daemon is unavailable to the official Harness: "
                + (docker.stderr.strip() or docker.stdout.strip())
            )
        docker_sdk_script = (
            "import docker, json; client = docker.from_env(); "
            "print(json.dumps(client.version(), sort_keys=True))"
        )
        docker_sdk = self._execute(
            [self.config.python_executable, "-c", docker_sdk_script]
        )
        if docker_sdk.returncode != 0:
            raise OfficialSweBenchHarnessError(
                "the official Harness Python environment cannot reach the Docker "
                "daemon through docker.from_env(): "
                + (docker_sdk.stderr.strip() or docker_sdk.stdout.strip())
            )
        try:
            docker_sdk_metadata = json.loads(
                docker_sdk.stdout.strip().splitlines()[-1]
            )
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise OfficialSweBenchHarnessError(
                "Docker SDK daemon metadata was not valid JSON"
            ) from exc
        return {
            "schema_version": "official_swebench_preflight.v1",
            "status": "contract_verified",
            "official_swebench_harness": True,
            "package": package_metadata,
            "config": asdict(self.config),
            "docker_version_sha256": _sha256_bytes(docker.stdout.encode("utf-8")),
            "docker_sdk": {
                "engine_version": docker_sdk_metadata.get("Version", ""),
                "os": docker_sdk_metadata.get("Os", ""),
                "architecture": docker_sdk_metadata.get("Arch", ""),
            },
            "required_cli_options": sorted(required_options),
        }

    @staticmethod
    def _load_frozen_row(benchmark_root: Path, instance_id: str) -> tuple[Dict[str, Any], str]:
        snapshot_path = benchmark_root / "snapshot.json"
        selection_path = benchmark_root / "pilot-selection.json"
        rows_path = benchmark_root / "raw" / "dev.rows.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        rows_bytes = rows_path.read_bytes()
        observed_hash = _sha256_bytes(rows_bytes)
        if observed_hash != snapshot.get("rows_sha256"):
            raise OfficialSweBenchHarnessError("frozen SWE-bench rows hash mismatch")
        if snapshot.get("dataset_revision") != selection.get("dataset_revision"):
            raise OfficialSweBenchHarnessError("snapshot and selection revisions differ")
        selected = {
            str(item.get("instance_id"))
            for item in selection.get("selected", [])
            if isinstance(item, dict)
        }
        if instance_id not in selected:
            raise KeyError(f"instance is not in the selected pilot: {instance_id}")
        raw = json.loads(rows_bytes)
        row_by_id = {
            str(item["row"].get("instance_id")): item["row"]
            for item in raw.get("rows", [])
            if isinstance(item, dict) and isinstance(item.get("row"), dict)
        }
        if instance_id not in row_by_id:
            raise KeyError(f"frozen raw row not found: {instance_id}")
        return row_by_id[instance_id], str(snapshot["dataset_revision"])

    def build_command(
        self,
        *,
        dataset_path: Path,
        predictions_path: Path,
        instance_id: str,
        run_id: str,
        runner_path: Optional[Path] = None,
        task_repo: Optional[Path] = None,
    ) -> List[str]:
        command = [self.config.python_executable]
        if runner_path is not None:
            command.append(str(runner_path))
        else:
            command.extend(["-m", HARNESS_MODULE])
        command.extend([
            "--dataset_name",
            str(dataset_path),
            "--split",
            "dev",
            "--instance_ids",
            instance_id,
            "--predictions_path",
            str(predictions_path),
            "--max_workers",
            str(self.config.max_workers),
            "--run_id",
            run_id,
            "--timeout",
            str(self.config.timeout_seconds),
            "--open_file_limit",
            str(self.config.open_file_limit),
            "--report_dir",
            str(dataset_path.parent),
            "--rewrite_reports",
            "False",
        ])
        if task_repo is not None:
            command.extend(["--task_repo", str(task_repo)])
        return command

    @staticmethod
    def _validate_task_repo(task_repo: Path, row: Dict[str, Any], instance_id: str) -> Dict[str, Any]:
        task_dir = task_repo / "tasks" / instance_id
        if not task_dir.is_dir():
            raise OfficialSweBenchHarnessError(f"official task repo task is missing: {task_dir}")
        task = {}
        for line in (task_dir / "task.yaml").read_text(encoding="utf-8").splitlines():
            if not line or line[0].isspace() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            task[key.strip()] = value.strip().strip("'\"")
        for field in ("instance_id", "repo", "base_commit", "version"):
            if str(task.get(field, "")) != str(row.get(field, "")):
                raise OfficialSweBenchHarnessError(
                    f"official task repo {field} mismatch for {instance_id}"
                )
        if row.get("split") and str(task.get("split", "")) != str(row["split"]):
            raise OfficialSweBenchHarnessError(f"official task repo split mismatch for {instance_id}")
        tests = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
        for field in ("FAIL_TO_PASS", "PASS_TO_PASS"):
            expected = row.get(field, "[]")
            expected = json.loads(expected) if isinstance(expected, str) else expected
            if tests.get(field) != expected:
                raise OfficialSweBenchHarnessError(
                    f"official task repo {field} mismatch for {instance_id}"
                )
        return {
            "head": subprocess.check_output(["git", "-C", str(task_repo), "rev-parse", "HEAD"], text=True).strip(),
            "task_sha256": _sha256_bytes((task_dir / "task.yaml").read_bytes()),
            "tests_sha256": _sha256_bytes((task_dir / "tests.json").read_bytes()),
            "eval_sha256": _sha256_bytes((task_dir / "eval.sh").read_bytes()),
            "dockerfile_sha256": _sha256_bytes((task_dir / "Dockerfile").read_bytes()),
        }

    @staticmethod
    def model_patch_from_workspace(
        workspace: Path | str, *, git_executable: str = "git"
    ) -> str:
        """Export tracked Episode changes without mutating the workspace."""
        root = Path(workspace).resolve()
        status = subprocess.run(
            [git_executable, "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if status.returncode != 0:
            raise OfficialSweBenchHarnessError(
                "cannot inspect Episode workspace Git state: " + status.stderr.strip()
            )
        untracked = [
            line[3:]
            for line in status.stdout.splitlines()
            if line.startswith("?? ")
        ]
        if untracked:
            raise OfficialSweBenchHarnessError(
                "official patch export refuses untracked candidate files: "
                + ", ".join(untracked)
            )
        diff = subprocess.run(
            [
                git_executable,
                "-C",
                str(root),
                "diff",
                "--binary",
                "--no-ext-diff",
                "HEAD",
                "--",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if diff.returncode != 0:
            raise OfficialSweBenchHarnessError(
                "cannot export Episode workspace patch: " + diff.stderr.strip()
            )
        if not diff.stdout.strip():
            raise OfficialSweBenchHarnessError("Episode workspace has no tracked patch")
        return diff.stdout

    def prepare(
        self,
        *,
        benchmark_root: Path | str,
        instance_id: str,
        model_patch: str,
        model_name_or_path: str,
        output_root: Path | str,
        run_id: Optional[str] = None,
        task_repo: Path | str | None = None,
    ) -> PreparedOfficialSweBenchRun:
        if not model_patch.strip():
            raise ValueError("candidate model patch must not be empty")
        if not model_name_or_path.strip():
            raise ValueError("model_name_or_path must not be empty")
        if any(char in instance_id for char in "\r\n\0"):
            raise ValueError("instance_id must be a single-line value")
        resolved_run_id = run_id or f"claw-{uuid.uuid4().hex[:16]}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", resolved_run_id):
            raise ValueError("run_id contains unsupported characters")
        root = Path(output_root).resolve()
        run_root = root / resolved_run_id
        if run_root.exists():
            raise FileExistsError(
                f"official Harness run directory already exists: {run_root}"
            )

        row, dataset_revision = self._load_frozen_row(
            Path(benchmark_root).resolve(), instance_id
        )
        resolved_task_repo = Path(task_repo).resolve() if task_repo is not None else None
        task_repo_metadata = (
            self._validate_task_repo(resolved_task_repo, row, instance_id)
            if resolved_task_repo is not None
            else None
        )
        run_root.mkdir(parents=True)
        dataset_path = run_root / "frozen-dataset.json"
        predictions_path = run_root / "predictions.jsonl"
        runner_path = run_root / "official-eval-runner.py"
        runner_path.write_text(RUNNER_SOURCE, encoding="utf-8")
        _write_json(dataset_path, [row])
        prediction = {
            "instance_id": instance_id,
            "model_name_or_path": model_name_or_path,
            "model_patch": model_patch,
        }
        predictions_path.write_text(
            json.dumps(prediction, sort_keys=True) + "\n", encoding="utf-8"
        )
        command = self.build_command(
            dataset_path=dataset_path,
            predictions_path=predictions_path,
            instance_id=instance_id,
            run_id=resolved_run_id,
            runner_path=runner_path,
            task_repo=resolved_task_repo,
        )
        manifest = {
            "schema_version": "official_swebench_input_manifest.v1",
            "status": "prepared",
            "official_swebench_harness": True,
            "run_id": resolved_run_id,
            "instance_id": instance_id,
            "dataset_revision": dataset_revision,
            "dataset_sha256": _sha256_bytes(dataset_path.read_bytes()),
            "task_repo": str(resolved_task_repo) if resolved_task_repo else None,
            "task_repo_metadata": task_repo_metadata,
            "prediction_sha256": _sha256_bytes(predictions_path.read_bytes()),
            "model_patch_sha256": _sha256_bytes(model_patch.encode("utf-8")),
            "prediction_fields": list(OFFICIAL_PREDICTION_FIELDS),
            "command": command,
            "claim_boundary": (
                "Inputs prepared for the official Harness; no evaluation has run."
            ),
        }
        input_manifest_path = run_root / "input-manifest.json"
        _write_json(input_manifest_path, manifest)
        return PreparedOfficialSweBenchRun(
            run_id=resolved_run_id,
            instance_id=instance_id,
            run_root=run_root,
            dataset_path=dataset_path,
            predictions_path=predictions_path,
            command=command,
            input_manifest_path=input_manifest_path,
            task_repo=resolved_task_repo,
        )

    def run(self, prepared: PreparedOfficialSweBenchRun) -> Dict[str, Any]:
        preflight = self.preflight()
        completed = self._execute(
            prepared.command,
            cwd=prepared.run_root,
            timeout=self.config.timeout_seconds + 300,
        )
        stdout_path = prepared.run_root / "harness.stdout.txt"
        stderr_path = prepared.run_root / "harness.stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        result_files = []
        for path in sorted(prepared.run_root.rglob("*.json")):
            if path in {prepared.dataset_path, prepared.input_manifest_path}:
                continue
            result_files.append(
                {
                    "path": path.relative_to(prepared.run_root).as_posix(),
                    "sha256": _sha256_bytes(path.read_bytes()),
                }
            )
        evidence = {
            "schema_version": "official_swebench_execution.v1",
            "status": "completed" if completed.returncode == 0 else "failed",
            "official_swebench_harness": True,
            "run_id": prepared.run_id,
            "instance_id": prepared.instance_id,
            "returncode": completed.returncode,
            "preflight": preflight,
            "stdout_sha256": _sha256_bytes(completed.stdout.encode("utf-8")),
            "stderr_sha256": _sha256_bytes(completed.stderr.encode("utf-8")),
            "result_files": result_files,
            "claim_boundary": (
                "Official Harness execution evidence; resolved status must be read "
                "from the archived Harness report, not inferred from process exit."
            ),
        }
        _write_json(prepared.run_root / "execution-evidence.json", evidence)
        return evidence
