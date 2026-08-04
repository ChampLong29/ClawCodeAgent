"""Load archived Episode artifacts into the Silver data pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Union

from ..dataset.builder import DatasetRecord
from ..episode.state import EpisodeManifest, EpisodeState
from ..experiment.artifacts import ArtifactStore
from ..experiment.schemas import TaskSpec, VerificationReport
from ..task_suite.registry import TaskSuiteManifest
from ..trajectory.schema import Trajectory


class EpisodeSourceError(ValueError):
    """Raised when an Episode bundle is incomplete or has inconsistent lineage."""


def _local_ref(episode_dir: Path, reference: str, *, kind: str) -> Path:
    if not isinstance(reference, str) or not reference.strip():
        raise EpisodeSourceError(f"episode has no {kind} reference")
    candidate = Path(reference)
    if not candidate.is_absolute():
        candidate = episode_dir / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(episode_dir)
    except ValueError as exc:
        raise EpisodeSourceError(
            f"{kind} reference escapes the Episode directory: {reference}"
        ) from exc
    if not resolved.is_file():
        raise EpisodeSourceError(f"{kind} artifact does not exist: {resolved}")
    return resolved


def load_episode_dataset_record(
    episode_dir: Union[str, Path],
    task: TaskSpec,
) -> DatasetRecord:
    """Load one verified, archived Episode and enforce its task lineage."""
    root = Path(episode_dir).resolve()
    if not root.is_dir():
        raise EpisodeSourceError(f"Episode directory does not exist: {root}")
    manifest = EpisodeManifest.load(root / "episode.json")
    if manifest.current_state != EpisodeState.ARCHIVED:
        raise EpisodeSourceError(
            f"Episode must be ARCHIVED, got {manifest.current_state.value}"
        )
    task.validate()
    expected_task_ref = f"{task.task_id}@{task.task_version}"
    if manifest.task_ref != expected_task_ref:
        raise EpisodeSourceError(
            f"task reference mismatch: expected {expected_task_ref}, "
            f"got {manifest.task_ref}"
        )
    if manifest.template_hash != task.template_hash:
        raise EpisodeSourceError("Episode template hash does not match TaskSpec")
    recorded_task_hash = manifest.metadata.get("task_content_hash")
    expected_task_hash = task.content_hash or task.compute_content_hash()
    if recorded_task_hash and recorded_task_hash != expected_task_hash:
        raise EpisodeSourceError("Episode task content hash does not match TaskSpec")

    trajectory_path = _local_ref(
        root, manifest.trajectory_ref or "", kind="trajectory"
    )
    verification_path = _local_ref(
        root, manifest.verification_ref or "", kind="verification"
    )
    trajectory = Trajectory.from_dict(
        json.loads(trajectory_path.read_text(encoding="utf-8"))
    )
    verification = VerificationReport.from_dict(
        json.loads(verification_path.read_text(encoding="utf-8"))
    )
    if trajectory.header.episode_id != manifest.episode_id:
        raise EpisodeSourceError("trajectory episode_id does not match Episode manifest")
    if trajectory.header.task_ref != manifest.task_ref:
        raise EpisodeSourceError("trajectory task_ref does not match Episode manifest")
    if verification.trajectory_ref != trajectory.header.trajectory_id:
        raise EpisodeSourceError("verification does not reference Episode trajectory")

    record = DatasetRecord(
        task=task,
        trajectory=trajectory,
        verification=verification,
        artifact_store=ArtifactStore(root / "artifacts"),
    )
    record.prepare()
    return record


def load_episode_batch(
    episode_dirs: Iterable[Union[str, Path]],
    suite: TaskSuiteManifest,
) -> List[DatasetRecord]:
    """Load an explicitly selected, deterministic batch from a task suite."""
    task_by_ref = {
        f"{task.task_id}@{task.task_version}": task for task in suite.tasks
    }
    records: List[DatasetRecord] = []
    seen_episode_ids = set()
    for source in sorted((Path(item).resolve() for item in episode_dirs), key=str):
        manifest = EpisodeManifest.load(source / "episode.json")
        if manifest.episode_id in seen_episode_ids:
            raise EpisodeSourceError(f"duplicate Episode: {manifest.episode_id}")
        try:
            task = task_by_ref[manifest.task_ref]
        except KeyError as exc:
            raise EpisodeSourceError(
                f"Episode task is absent from suite: {manifest.task_ref}"
            ) from exc
        records.append(load_episode_dataset_record(source, task))
        seen_episode_ids.add(manifest.episode_id)
    if not records:
        raise EpisodeSourceError("Episode batch must not be empty")
    return records
