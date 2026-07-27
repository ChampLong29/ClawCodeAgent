"""Migration of legacy rollout records into append-only trajectory v2."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Optional, Union

from ..experiment.artifacts import ArtifactStore
from .schema import (
    TRAJECTORY_SCHEMA_VERSION,
    Trajectory,
    TrajectoryHeader,
    stable_id,
    utc_now,
)


class TrajectoryMigrationError(ValueError):
    """Raised when an input trajectory format cannot be migrated."""


_STOP_REASON_MAP = {
    "completed": "completed",
    "failed": "failed",
    "error": "failed",
    "timeout": "timeout",
    "timed_out": "timeout",
    "budget_exceeded": "budget_exceeded",
    "cancelled": "cancelled",
    "stopped": "cancelled",
}


def _as_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return copy.deepcopy(result)
    if hasattr(result, "to_dict"):
        return copy.deepcopy(result.to_dict())
    raise TrajectoryMigrationError(
        "legacy rollout must be a dictionary or expose to_dict()"
    )


def _maybe_offload(
    payload: Dict[str, Any],
    artifact_store: Optional[ArtifactStore],
    threshold_bytes: int,
):
    if artifact_store is None:
        return payload, []
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) <= threshold_bytes:
        return payload, []
    artifact = artifact_store.put_json(payload)
    return {
        "artifact_ref": artifact.uri,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }, [artifact]


def rollout_result_to_trajectory(
    result: Any,
    *,
    model_version: str = "legacy-unknown",
    runtime_version: str = "legacy-rollout.v1",
    prompt_version: str = "legacy-unknown",
    tool_version: str = "legacy-unknown",
    config_version: str = "legacy-unknown",
    artifact_store: Optional[ArtifactStore] = None,
    artifact_threshold_bytes: int = 16_384,
) -> Trajectory:
    """Convert a legacy ``RolloutResult`` without mixing reward into facts."""

    data = _as_dict(result)
    task_id = str(data.get("task_id") or "unknown-task")
    session_id = str(data.get("session_id") or "unknown-session")
    identity = {
        "task_id": task_id,
        "session_id": session_id,
        "messages": data.get("messages", []),
        "stop_reason": data.get("stop_reason", ""),
    }
    episode_id = stable_id("ep", {"task_id": task_id, "session_id": session_id})
    trajectory_id = stable_id("traj", identity)
    started_at = str(data.get("started_at") or utc_now())

    trajectory = Trajectory(
        header=TrajectoryHeader(
            trajectory_id=trajectory_id,
            episode_id=episode_id,
            task_ref=task_id,
            model_version=model_version,
            runtime_version=runtime_version,
            prompt_version=prompt_version,
            tool_version=tool_version,
            config_version=config_version,
            artifact_base=(
                str(artifact_store.root) if artifact_store is not None else ""
            ),
            started_at=started_at,
        )
    )
    trajectory.append(
        "episode_started",
        payload={
            "task_id": task_id,
            "session_id": session_id,
            "migrated_from": "rollout_result.v1",
        },
    )

    tool_calls: Dict[str, str] = {}
    messages = data.get("messages") or []
    for index, message in enumerate(messages):
        role = str(message.get("role", "unknown"))
        message_payload, artifact_refs = _maybe_offload(
            {"message_index": index, "message": message},
            artifact_store,
            artifact_threshold_bytes,
        )
        if role == "assistant":
            response = trajectory.append(
                "model_response",
                payload=message_payload,
                artifact_refs=artifact_refs,
            )
            for tool_index, call in enumerate(message.get("tool_calls") or []):
                call_id = str(
                    call.get("id")
                    or stable_id(
                        "call",
                        {
                            "trajectory_id": trajectory_id,
                            "message_index": index,
                            "tool_index": tool_index,
                            "call": call,
                        },
                    )
                )
                call_payload, call_refs = _maybe_offload(
                    {"call": call}, artifact_store, artifact_threshold_bytes
                )
                tool_event = trajectory.append(
                    "tool_call",
                    payload={"call_id": call_id, **call_payload},
                    parent_event_id=response.event_id,
                    artifact_refs=call_refs,
                )
                tool_calls[call_id] = tool_event.event_id
        elif role == "tool":
            call_id = str(
                message.get("tool_call_id") or message.get("call_id") or ""
            )
            trajectory.append(
                "tool_result",
                payload={"call_id": call_id or None, **message_payload},
                parent_event_id=tool_calls.get(call_id),
                artifact_refs=artifact_refs,
            )
        else:
            trajectory.append(
                "model_request",
                payload={
                    **message_payload,
                    "inferred_from_legacy_message": True,
                },
                artifact_refs=artifact_refs,
            )

    if data.get("diff_result") is not None:
        payload, refs = _maybe_offload(
            {"diff_result": data["diff_result"]},
            artifact_store,
            artifact_threshold_bytes,
        )
        trajectory.append("workspace_diff", payload=payload, artifact_refs=refs)
    if data.get("test_result") is not None:
        payload, refs = _maybe_offload(
            {"test_result": data["test_result"]},
            artifact_store,
            artifact_threshold_bytes,
        )
        trajectory.append("test_result", payload=payload, artifact_refs=refs)
    if data.get("error"):
        trajectory.append("runtime_error", payload={"error": str(data["error"])})

    raw_reason = str(data.get("stop_reason") or "failed")
    reason = _STOP_REASON_MAP.get(raw_reason, "failed")
    trajectory.terminate(
        reason,
        detail=(
            str(data.get("error"))
            if data.get("error")
            else f"legacy stop_reason={raw_reason}"
        ),
        usage=dict(data.get("usage") or {}),
        cost={"execution_time_seconds": float(data.get("execution_time") or 0.0)},
    )
    return trajectory


class TrajectoryMigrator:
    """Registry-style facade for current and legacy trajectory records."""

    @staticmethod
    def migrate(
        data: Union[Trajectory, Dict[str, Any]],
        **conversion_options: Any,
    ) -> Trajectory:
        if isinstance(data, Trajectory):
            data.validate()
            return data
        if not isinstance(data, dict):
            raise TrajectoryMigrationError("trajectory input must be an object")

        header = data.get("header")
        if isinstance(header, dict):
            version = header.get("schema_version")
            if version == TRAJECTORY_SCHEMA_VERSION:
                return Trajectory.from_dict(data)
            raise TrajectoryMigrationError(
                f"no migration registered for trajectory schema {version!r}"
            )

        if "task_id" in data and "messages" in data:
            return rollout_result_to_trajectory(data, **conversion_options)

        raise TrajectoryMigrationError("unrecognized legacy trajectory shape")
