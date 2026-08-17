"""Leakage-safe boundary for the curated SWE-bench Lite Dev pilot.

The agent-facing catalog and evaluation-only raw rows are intentionally loaded
through separate methods.  A rollout caller can use :meth:`load_agent_tasks`
without ever reading gold patches, test patches, hints, or test identifiers.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from ..episode.checkpoint import validate_workspace_symlinks, workspace_hash
from ..experiment.schemas import canonical_hash
from ..experiment.schemas import TaskSpec


AGENT_INPUT_SCHEMA_VERSION = "swe_bench_lite_agent_input.v1"
EVALUATION_BUNDLE_SCHEMA_VERSION = "swe_bench_lite_evaluation_bundle.v1"
LOCAL_CALIBRATION_SCHEMA_VERSION = "swe_bench_lite_local_calibration.v1"
EPISODE_EVALUATOR_ASSET_SCHEMA_VERSION = "swe_bench_lite_episode_evaluator.v1"
FORBIDDEN_AGENT_FIELDS = {
    "patch",
    "test_patch",
    "hints_text",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
}


def _read_object(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _patch_target_paths(content: str) -> List[Path]:
    """Extract destination paths from a unified diff without reading Git state."""
    targets: List[Path] = []
    for line in content.splitlines():
        if not line.startswith("+++ "):
            continue
        raw = line[4:].split("\t", 1)[0].strip()
        if raw == "/dev/null":
            continue
        if raw.startswith("b/"):
            raw = raw[2:]
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"patch contains an unsafe target path: {raw}")
        targets.append(path)
    if not targets:
        raise ValueError("patch contains no destination paths")
    return targets


def _parse_test_ids(value: Any, *, field_name: str) -> List[str]:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON string list")
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) and item.strip() for item in parsed
    ):
        raise ValueError(f"{field_name} must contain non-empty string IDs")
    return parsed


@dataclass(frozen=True)
class SweBenchLiteAgentTask:
    """Public issue context that is safe to place in an agent workspace."""

    instance_id: str
    repo: str
    base_commit: str
    version: str
    problem_statement: str
    dataset_revision: str
    workspace_path: Path
    license: str
    schema_version: str = AGENT_INPUT_SCHEMA_VERSION

    def validate(self) -> None:
        for name in ("instance_id", "repo", "version", "problem_statement", "license"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if len(self.base_commit) != 40 or len(self.dataset_revision) != 40:
            raise ValueError("base_commit and dataset_revision must be 40-char commits")
        if not self.workspace_path.is_dir():
            raise FileNotFoundError(self.workspace_path)

    def to_agent_payload(self) -> Dict[str, str]:
        """Return only fields allowed to cross the agent-context boundary."""
        self.validate()
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "version": self.version,
            "problem_statement": self.problem_statement,
        }


@dataclass(frozen=True)
class SweBenchLiteEvaluationBundle:
    """Evaluation-only secrets loaded after an agent rollout has finished."""

    instance_id: str
    dataset_revision: str
    patch: str
    test_patch: str
    hints_text: str
    fail_to_pass: List[str]
    pass_to_pass: List[str]
    schema_version: str = EVALUATION_BUNDLE_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.instance_id.strip():
            raise ValueError("instance_id must not be empty")
        if len(self.dataset_revision) != 40:
            raise ValueError("dataset_revision must be a 40-char commit")
        if not self.patch.strip() or not self.test_patch.strip():
            raise ValueError("gold patch and test patch must not be empty")
        if not self.fail_to_pass or not self.pass_to_pass:
            raise ValueError("evaluation test ID sets must not be empty")

    def to_fingerprint(self) -> Dict[str, Any]:
        """Return auditable hashes without serializing evaluation secrets."""
        self.validate()
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "dataset_revision": self.dataset_revision,
            "patch_sha256": _sha256_text(self.patch),
            "test_patch_sha256": _sha256_text(self.test_patch),
            "hints_sha256": _sha256_text(self.hints_text),
            "fail_to_pass_count": len(self.fail_to_pass),
            "pass_to_pass_count": len(self.pass_to_pass),
            "test_ids_hash": canonical_hash(
                {
                    "fail_to_pass": self.fail_to_pass,
                    "pass_to_pass": self.pass_to_pass,
                }
            ),
        }


@dataclass(frozen=True)
class SweBenchLiteTestExecution:
    group: str
    returncode: int
    test_count: int
    duration_seconds: float
    stdout_sha256: str
    stderr_sha256: str
    normalized_node_ids: int = 0

    @property
    def passed(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group": self.group,
            "returncode": self.returncode,
            "passed": self.passed,
            "test_count": self.test_count,
            "normalized_node_ids": self.normalized_node_ids,
            "duration_seconds": round(self.duration_seconds, 6),
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
        }


def _normalize_pytest_node_ids(test_ids: List[str]) -> tuple[List[str], int]:
    """Conservatively expand visibly truncated parameter IDs to the test function.

    Some historical SWE-bench rows contain a parameterized pytest node ID with an
    opening ``[`` but no closing ``]``.  Pytest rejects the entire invocation with
    exit code 4. Running the full test function is a conservative superset of the
    intended parameter case and avoids silently dropping regression coverage.
    """
    normalized: List[str] = []
    changes = 0
    for node_id in test_ids:
        candidate = node_id
        if "[" in candidate and "]" not in candidate.rsplit("::", 1)[-1]:
            candidate = candidate.split("[", 1)[0]
            changes += 1
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized, changes


@dataclass(frozen=True)
class SweBenchLiteLocalCalibrationResult:
    instance_id: str
    dataset_revision: str
    base_commit: str
    python_version: str
    evaluator_fingerprint: Dict[str, Any]
    baseline_fail_to_pass: SweBenchLiteTestExecution
    baseline_pass_to_pass: SweBenchLiteTestExecution
    reference_fail_to_pass: SweBenchLiteTestExecution
    reference_pass_to_pass: SweBenchLiteTestExecution
    schema_version: str = LOCAL_CALIBRATION_SCHEMA_VERSION

    @property
    def passed(self) -> bool:
        return (
            not self.baseline_fail_to_pass.passed
            and self.baseline_pass_to_pass.passed
            and self.reference_fail_to_pass.passed
            and self.reference_pass_to_pass.passed
        )

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": "passed" if self.passed else "failed",
            "instance_id": self.instance_id,
            "dataset_revision": self.dataset_revision,
            "base_commit": self.base_commit,
            "environment": {
                "python_version": self.python_version,
                "official_swebench_harness": False,
                "isolation": "git_archive_snapshot_without_git_metadata",
            },
            "evaluator_fingerprint": self.evaluator_fingerprint,
            "runs": {
                "baseline_fail_to_pass": self.baseline_fail_to_pass.to_dict(),
                "baseline_pass_to_pass": self.baseline_pass_to_pass.to_dict(),
                "reference_fail_to_pass": self.reference_fail_to_pass.to_dict(),
                "reference_pass_to_pass": self.reference_pass_to_pass.to_dict(),
            },
            "claim_boundary": (
                "Local compatibility calibration only; not an official SWE-bench score."
            ),
        }


@dataclass(frozen=True)
class MaterializedSweBenchLiteEpisodeTask:
    """Agent-safe template plus evaluator-only assets for one Dev Episode."""

    task_spec: TaskSpec
    template_root: Path
    evaluator_assets_root: Path
    evaluator_fingerprint: Dict[str, Any]


@dataclass(frozen=True)
class SweBenchLiteCandidateEvaluation:
    instance_id: str
    fail_to_pass: SweBenchLiteTestExecution
    pass_to_pass: SweBenchLiteTestExecution

    @property
    def passed(self) -> bool:
        return self.fail_to_pass.passed and self.pass_to_pass.passed

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "schema_version": "swe_bench_lite_candidate_evaluation.v1",
            "status": "passed" if self.passed else "failed",
            "instance_id": self.instance_id,
            "official_swebench_harness": False,
            "runs": {
                "fail_to_pass": self.fail_to_pass.to_dict(),
                "pass_to_pass": self.pass_to_pass.to_dict(),
            },
            "claim_boundary": (
                "Local candidate verification only; not an official SWE-bench score."
            ),
        }


class LocalSweBenchLiteCalibrationRunner:
    """Calibrate one snapshot without exposing evaluator assets to an agent.

    This runner is intentionally not the official Docker harness.  It proves
    that the pinned local environment observes the expected baseline failure
    and reference-patch success before any paid rollout is attempted.
    """

    def __init__(self, git_executable: str = "git"):
        self.git_executable = git_executable

    def _git(self, repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.git_executable, "-C", str(repository), *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )

    def prepare_workspace(
        self, task: SweBenchLiteAgentTask, destination: Path | str
    ) -> Path:
        task.validate()
        source = task.workspace_path.resolve()
        target = Path(destination).resolve()
        if target == source or source in target.parents or target in source.parents:
            raise ValueError("calibration workspace must be separate from source snapshot")
        if target.exists():
            raise FileExistsError(target)

        head = self._git(source, "rev-parse", "HEAD")
        if head.returncode != 0 or head.stdout.strip() != task.base_commit:
            raise RuntimeError(f"source snapshot HEAD mismatch: {head.stderr.strip()}")
        archived = subprocess.run(
            [
                self.git_executable,
                "-C",
                str(source),
                "archive",
                "--format=tar",
                task.base_commit,
            ],
            capture_output=True,
            check=False,
        )
        if archived.returncode != 0:
            raise RuntimeError(
                "failed to archive source snapshot: "
                + archived.stderr.decode("utf-8", errors="replace").strip()
            )
        target.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(archived.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                member_target = (target / member.name).resolve()
                if target != member_target and target not in member_target.parents:
                    raise RuntimeError("git archive contains an unsafe member path")
            archive.extractall(target)
        return target

    def _apply_patch(self, workspace: Path, content: str, *, label: str) -> None:
        target_paths = _patch_target_paths(content)
        before = {
            path: _sha256_file(workspace / path)
            if (workspace / path).is_file()
            else None
            for path in target_paths
        }
        environment = dict(os.environ)
        # Calibration workspaces often live below the caller's Git repository.
        # Stop repository discovery at their parent so paths remain relative to
        # the isolated snapshot instead of the outer project root.
        environment["GIT_CEILING_DIRECTORIES"] = str(workspace.parent.resolve())
        completed = subprocess.run(
            [
                self.git_executable,
                "apply",
                "--no-index",
                "--whitespace=nowarn",
                "-",
            ],
            cwd=str(workspace),
            env=environment,
            input=content,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"failed to apply {label}: {completed.stderr.strip()}")
        unchanged = []
        for path in target_paths:
            target = workspace / path
            after = _sha256_file(target) if target.is_file() else None
            if after == before[path]:
                unchanged.append(path.as_posix())
        if unchanged:
            raise RuntimeError(
                f"{label} reported success without changing targets: {unchanged}"
            )

    def _run_tests(
        self,
        workspace: Path,
        python_executable: Path,
        test_ids: List[str],
        *,
        group: str,
        timeout_seconds: float,
    ) -> SweBenchLiteTestExecution:
        environment = dict(os.environ)
        python_paths = []
        source = workspace / "src"
        if source.is_dir():
            python_paths.append(str(source))
        python_paths.append(str(workspace))
        existing_path = environment.get("PYTHONPATH")
        if existing_path:
            python_paths.extend(
                item
                for item in existing_path.split(os.pathsep)
                if item and item not in python_paths
            )
        environment["PYTHONPATH"] = os.pathsep.join(python_paths)
        normalized_ids, normalization_count = _normalize_pytest_node_ids(test_ids)
        started = time.monotonic()
        completed = subprocess.run(
            [str(python_executable), "-m", "pytest", "-q", *normalized_ids],
            cwd=str(workspace),
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        return SweBenchLiteTestExecution(
            group=group,
            returncode=completed.returncode,
            test_count=len(test_ids),
            duration_seconds=time.monotonic() - started,
            stdout_sha256=_sha256_text(completed.stdout),
            stderr_sha256=_sha256_text(completed.stderr),
            normalized_node_ids=normalization_count,
        )

    def calibrate_reference(
        self,
        task: SweBenchLiteAgentTask,
        bundle: SweBenchLiteEvaluationBundle,
        *,
        python_executable: Path | str,
        workspace: Path | str,
        timeout_seconds: float = 300.0,
    ) -> SweBenchLiteLocalCalibrationResult:
        if task.instance_id != bundle.instance_id:
            raise ValueError("task and evaluator bundle instance IDs differ")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        # Keep a virtualenv's python symlink intact.  ``Path.resolve()`` follows
        # it to the base interpreter on POSIX and silently drops the venv site
        # packages (including pytest).
        python_path = Path(python_executable).expanduser().absolute()
        if not python_path.is_file():
            raise FileNotFoundError(python_path)
        target = self.prepare_workspace(task, workspace)
        self._apply_patch(target, bundle.test_patch, label="test patch")
        baseline_ftp = self._run_tests(
            target,
            python_path,
            bundle.fail_to_pass,
            group="baseline_fail_to_pass",
            timeout_seconds=timeout_seconds,
        )
        baseline_ptp = self._run_tests(
            target,
            python_path,
            bundle.pass_to_pass,
            group="baseline_pass_to_pass",
            timeout_seconds=timeout_seconds,
        )
        self._apply_patch(target, bundle.patch, label="reference patch")
        reference_ftp = self._run_tests(
            target,
            python_path,
            bundle.fail_to_pass,
            group="reference_fail_to_pass",
            timeout_seconds=timeout_seconds,
        )
        reference_ptp = self._run_tests(
            target,
            python_path,
            bundle.pass_to_pass,
            group="reference_pass_to_pass",
            timeout_seconds=timeout_seconds,
        )
        version = subprocess.run(
            [str(python_path), "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        python_version = (version.stdout or version.stderr).strip()
        return SweBenchLiteLocalCalibrationResult(
            instance_id=task.instance_id,
            dataset_revision=task.dataset_revision,
            base_commit=task.base_commit,
            python_version=python_version,
            evaluator_fingerprint=bundle.to_fingerprint(),
            baseline_fail_to_pass=baseline_ftp,
            baseline_pass_to_pass=baseline_ptp,
            reference_fail_to_pass=reference_ftp,
            reference_pass_to_pass=reference_ptp,
        )


def evaluate_swe_bench_lite_candidate(
    candidate_workspace: Path | str,
    evaluator_asset: Path | str,
    *,
    python_executable: Path | str,
    timeout_seconds: float = 300.0,
) -> SweBenchLiteCandidateEvaluation:
    """Evaluate a candidate in a disposable copy with hidden tests mounted.

    The Test Patch and test IDs are read only after the agent run, copied into
    a temporary evaluator workspace, and never applied to the Episode workspace.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    source = Path(candidate_workspace).resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    asset = _read_object(Path(evaluator_asset).resolve())
    if asset.get("schema_version") != EPISODE_EVALUATOR_ASSET_SCHEMA_VERSION:
        raise ValueError("unsupported SWE-bench episode evaluator asset")
    instance_id = str(asset.get("instance_id", ""))
    test_patch = str(asset.get("test_patch", ""))
    fail_to_pass = asset.get("fail_to_pass")
    pass_to_pass = asset.get("pass_to_pass")
    if not instance_id or not test_patch.strip():
        raise ValueError("evaluator asset is missing instance or Test Patch")
    if not isinstance(fail_to_pass, list) or not all(
        isinstance(item, str) and item for item in fail_to_pass
    ):
        raise ValueError("evaluator fail_to_pass must contain test IDs")
    if not isinstance(pass_to_pass, list) or not all(
        isinstance(item, str) and item for item in pass_to_pass
    ):
        raise ValueError("evaluator pass_to_pass must contain test IDs")

    python_path = Path(python_executable).expanduser().absolute()
    if not python_path.is_file():
        raise FileNotFoundError(python_path)
    runner = LocalSweBenchLiteCalibrationRunner()
    with tempfile.TemporaryDirectory(prefix="claw-swebench-eval-") as temporary:
        evaluation_root = Path(temporary) / "workspace"

        def ignore(_directory: str, names: List[str]) -> List[str]:
            blocked = {
                ".git",
                ".claw_hidden_tests",
                ".port_sessions",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
            }
            return [name for name in names if name in blocked]

        validate_workspace_symlinks(source)
        shutil.copytree(source, evaluation_root, ignore=ignore, symlinks=True)
        runner._apply_patch(evaluation_root, test_patch, label="test patch")
        ftp = runner._run_tests(
            evaluation_root,
            python_path,
            list(fail_to_pass),
            group="fail_to_pass",
            timeout_seconds=timeout_seconds,
        )
        ptp = runner._run_tests(
            evaluation_root,
            python_path,
            list(pass_to_pass),
            group="pass_to_pass",
            timeout_seconds=timeout_seconds,
        )
    return SweBenchLiteCandidateEvaluation(
        instance_id=instance_id,
        fail_to_pass=ftp,
        pass_to_pass=ptp,
    )


class SweBenchLiteEpisodeTaskMaterializer:
    """Build a Dev TaskSpec without placing evaluator secrets in agent context."""

    def __init__(self, evaluator_script: Path | str):
        self.evaluator_script = Path(evaluator_script).resolve()
        if not self.evaluator_script.is_file():
            raise FileNotFoundError(self.evaluator_script)

    @staticmethod
    def _shell_command(parts: List[str]) -> str:
        if os.name == "nt":
            return subprocess.list2cmdline(parts)
        return " ".join(shlex.quote(part) for part in parts)

    def materialize(
        self,
        task: SweBenchLiteAgentTask,
        bundle: SweBenchLiteEvaluationBundle,
        *,
        output_root: Path | str,
        python_executable: Path | str,
        timeout_seconds: float = 300.0,
    ) -> MaterializedSweBenchLiteEpisodeTask:
        if task.instance_id != bundle.instance_id:
            raise ValueError("task and evaluator bundle instance IDs differ")
        task.validate()
        bundle.validate()
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        root = Path(output_root).resolve()
        if root.exists():
            raise FileExistsError(root)
        template = root / "template"
        evaluator = root / "evaluator"
        LocalSweBenchLiteCalibrationRunner().prepare_workspace(task, template)
        evaluator.mkdir(parents=True)
        asset_path = evaluator / "evaluation.json"
        asset_payload = {
            "schema_version": EPISODE_EVALUATOR_ASSET_SCHEMA_VERSION,
            "instance_id": task.instance_id,
            "dataset_revision": task.dataset_revision,
            "test_patch": bundle.test_patch,
            "fail_to_pass": list(bundle.fail_to_pass),
            "pass_to_pass": list(bundle.pass_to_pass),
        }
        asset_path.write_text(
            json.dumps(asset_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        python_path = Path(python_executable).expanduser().absolute()
        if not python_path.is_file():
            raise FileNotFoundError(python_path)
        command = self._shell_command(
            [
                str(Path(sys.executable).absolute()),
                str(self.evaluator_script),
                "--asset",
                ".claw_hidden_tests/evaluation.json",
                "--python",
                str(python_path),
                "--timeout",
                str(timeout_seconds),
            ]
        )
        spec = TaskSpec(
            task_id=task.instance_id,
            task_version=f"{task.version}+{task.dataset_revision[:12]}",
            family_id=f"swe-bench-lite:{task.repo}",
            domain="python-real-repository",
            task_type="fix_bug",
            difficulty="real_issue",
            split="dev",
            prompt=(
                "Fix the reported issue in the current repository checkout. "
                "Inspect the local source, reproduce with the configured Python "
                "environment when useful, make the smallest correct source change, "
                "and finish with a concise summary. Every tool already starts in the "
                "working directory shown in Environment Context: use relative paths "
                "and never guess /workspace or switch to another checkout. The "
                "configured `python` has already passed an import-path preflight for "
                "this workspace; do not inspect virtualenv internals or editable-install "
                "metadata unless an actual import command fails. Work only inside the "
                "current workspace; do not browse the web or search other filesystem roots. "
                "Hidden regression tests run after you finish, so prioritize editing "
                "and validating the implementation over version archaeology. Once "
                "targeted tests pass and the diff is minimal, stop calling tools and "
                "return the final summary immediately.\n\n"
                "Reported issue:\n"
                + task.problem_statement
            ),
            template_ref=str(template),
            template_hash=workspace_hash(template),
            initial_checks=[command],
            test_commands=[command],
            timeout_seconds=timeout_seconds,
            source=f"SWE-bench Lite@{task.dataset_revision}",
            license=task.license,
            test_assets_ref=str(evaluator),
            test_assets_hash=workspace_hash(evaluator),
            resource_limits={"processes": 8},
            tags=["swe-bench-lite", "real-repository", task.repo],
        )
        spec.content_hash = spec.compute_content_hash()
        spec.validate()
        return MaterializedSweBenchLiteEpisodeTask(
            task_spec=spec,
            template_root=template,
            evaluator_assets_root=evaluator,
            evaluator_fingerprint=bundle.to_fingerprint(),
        )


class SweBenchLiteDevAdapter:
    """Load public rollout inputs and private evaluator inputs independently."""

    def __init__(self, benchmark_root: Path | str):
        self.root = Path(benchmark_root).resolve()
        self.agent_inputs_path = self.root / "pilot-agent-inputs.json"
        self.selection_path = self.root / "pilot-selection.json"
        self.snapshots_path = self.root / "repo-snapshots.json"
        self.raw_rows_path = self.root / "raw" / "dev.rows.json"

    def load_agent_tasks(self) -> List[SweBenchLiteAgentTask]:
        """Load tasks without opening ``raw/dev.rows.json``."""
        public = _read_object(self.agent_inputs_path)
        selection = _read_object(self.selection_path)
        snapshots = _read_object(self.snapshots_path)
        revision = str(public.get("dataset_revision", ""))
        if revision != selection.get("dataset_revision"):
            raise ValueError("agent input and selection revisions differ")

        selected = {
            item["instance_id"]: item for item in selection.get("selected", [])
        }
        snapshot_by_id = {
            item["instance_id"]: item for item in snapshots.get("instances", [])
        }
        tasks: List[SweBenchLiteAgentTask] = []
        for payload in public.get("instances", []):
            if not isinstance(payload, dict):
                raise ValueError("agent instances must be JSON objects")
            forbidden = FORBIDDEN_AGENT_FIELDS.intersection(payload)
            if forbidden:
                raise ValueError(f"agent input contains evaluation fields: {sorted(forbidden)}")
            instance_id = str(payload.get("instance_id", ""))
            selected_item = selected.get(instance_id)
            snapshot = snapshot_by_id.get(instance_id)
            if selected_item is None or snapshot is None:
                raise ValueError(f"missing selection or snapshot for {instance_id}")
            if payload.get("base_commit") != snapshot.get("head"):
                raise ValueError(f"snapshot HEAD mismatch for {instance_id}")
            workspace_path = self.root / str(selected_item["local_repo"])
            task = SweBenchLiteAgentTask(
                instance_id=instance_id,
                repo=str(payload["repo"]),
                base_commit=str(payload["base_commit"]),
                version=str(payload["version"]),
                problem_statement=str(payload["problem_statement"]),
                dataset_revision=revision,
                workspace_path=workspace_path.resolve(),
                license=str(selected_item["license"]),
            )
            task.validate()
            tasks.append(task)

        if [task.instance_id for task in tasks] != list(selected):
            raise ValueError("agent task order or membership differs from selection")
        return tasks

    def load_evaluation_bundle(self, instance_id: str) -> SweBenchLiteEvaluationBundle:
        """Explicitly cross the evaluator boundary and load one private row."""
        public = _read_object(self.agent_inputs_path)
        known = {
            str(item.get("instance_id")) for item in public.get("instances", [])
        }
        if instance_id not in known:
            raise KeyError(f"instance is not in the selected pilot: {instance_id}")

        raw = _read_object(self.raw_rows_path)
        rows = {
            str(item["row"]["instance_id"]): item["row"]
            for item in raw.get("rows", [])
            if isinstance(item, dict) and isinstance(item.get("row"), dict)
        }
        row = rows.get(instance_id)
        if row is None:
            raise KeyError(f"raw evaluation row not found: {instance_id}")
        bundle = SweBenchLiteEvaluationBundle(
            instance_id=instance_id,
            dataset_revision=str(public["dataset_revision"]),
            patch=str(row.get("patch", "")),
            test_patch=str(row.get("test_patch", "")),
            hints_text=str(row.get("hints_text", "")),
            fail_to_pass=_parse_test_ids(
                row.get("FAIL_TO_PASS"), field_name="FAIL_TO_PASS"
            ),
            pass_to_pass=_parse_test_ids(
                row.get("PASS_TO_PASS"), field_name="PASS_TO_PASS"
            ),
        )
        bundle.validate()
        return bundle
