"""Trajectory validation and assistant-only tool-use SFT conversion."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from ..experiment.artifacts import ArtifactStore
from ..trajectory.schema import Trajectory


class DatasetValidationError(ValueError):
    """Raised when a trajectory cannot safely become a training sample."""


def _resolved_payload(event, artifact_store: Optional[ArtifactStore]):
    payload = dict(event.payload)
    if artifact_store and payload.get("artifact_ref"):
        resolved = artifact_store.get_json(payload["artifact_ref"])
        if isinstance(resolved, dict):
            merged = dict(payload)
            merged.update(resolved)
            return merged
    return payload


def extract_messages(
    trajectory: Trajectory,
    *,
    artifact_store: Optional[ArtifactStore] = None,
) -> List[Dict[str, Any]]:
    """Reconstruct chat messages without duplicating tool-call events."""
    trajectory.validate()
    messages: List[Dict[str, Any]] = []
    response_indices: Dict[str, int] = {}
    seen_call_ids = set()

    for event in trajectory.events:
        payload = _resolved_payload(event, artifact_store)
        if event.event_type == "model_request":
            # Runtime adapter v2 records the complete request history on every
            # model call. Treat that snapshot as authoritative so earlier
            # response/tool events are not duplicated. Legacy trajectories
            # instead store one message per request and remain append-only.
            request_messages = payload.get("messages")
            if isinstance(request_messages, list):
                messages = copy.deepcopy(request_messages)
                response_indices.clear()
                seen_call_ids = {
                    str(call.get("id"))
                    for message in messages
                    if isinstance(message, dict)
                    for call in message.get("tool_calls") or []
                    if isinstance(call, dict) and call.get("id")
                }
            else:
                message = payload.get("message")
                if isinstance(message, dict):
                    messages.append(copy.deepcopy(message))
        elif event.event_type == "model_response":
            message = payload.get("message")
            if not isinstance(message, dict) and (
                "content" in payload or "tool_calls" in payload
            ):
                message = {
                    "role": "assistant",
                    "content": payload.get("content") or "",
                    "tool_calls": copy.deepcopy(payload.get("tool_calls") or []),
                }
            if isinstance(message, dict):
                messages.append(copy.deepcopy(message))
                response_indices[event.event_id] = len(messages) - 1
                if event.parent_event_id:
                    response_indices[event.parent_event_id] = len(messages) - 1
                for call in message.get("tool_calls") or []:
                    call_id = call.get("id")
                    if call_id:
                        seen_call_ids.add(str(call_id))
        elif event.event_type == "tool_call":
            call_id = str(payload.get("call_id") or "")
            if call_id in seen_call_ids:
                continue
            parent_index = response_indices.get(event.parent_event_id or "")
            if parent_index is None:
                raise DatasetValidationError(
                    f"tool_call {call_id!r} has no model_response parent"
                )
            if not call_id:
                raise DatasetValidationError("tool_call event is missing call_id")
            call = copy.deepcopy(payload.get("call") or {})
            if not call and payload.get("tool_name"):
                call = {
                    "id": call_id,
                    "name": payload.get("tool_name"),
                    "arguments": copy.deepcopy(payload.get("arguments") or {}),
                }
            if not isinstance(call, dict):
                raise DatasetValidationError("tool_call payload must be an object")
            parent_calls = messages[parent_index].setdefault("tool_calls", [])
            matched = False
            for existing in parent_calls:
                if existing.get("id"):
                    continue
                if existing == call:
                    existing["id"] = call_id
                    matched = True
                    break
            if not matched:
                call.setdefault("id", call_id)
                parent_calls.append(call)
            seen_call_ids.add(call_id)
        elif event.event_type == "tool_result":
            message = payload.get("message")
            if isinstance(message, dict):
                message = copy.deepcopy(message)
                message.setdefault("tool_call_id", payload.get("call_id"))
                messages.append(message)
            else:
                result = payload.get("result", payload)
                content = (
                    result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False, sort_keys=True)
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": payload.get("call_id"),
                        "name": payload.get("tool_name")
                        or payload.get("actual_tool_name"),
                        "content": content,
                    }
                )
    if not messages:
        raise DatasetValidationError("trajectory contains no reconstructable messages")
    validate_tool_alignment(messages)
    return messages


def validate_tool_alignment(messages: List[Dict[str, Any]]) -> None:
    calls: Dict[str, int] = {}
    results: Dict[str, int] = {}
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                call_id = str(call.get("id") or "")
                if not call_id:
                    raise DatasetValidationError(
                        f"assistant tool call at message {index} is missing id"
                    )
                if call_id in calls:
                    raise DatasetValidationError(f"duplicate tool call id: {call_id}")
                calls[call_id] = index
        elif role == "tool":
            call_id = str(
                message.get("tool_call_id") or message.get("call_id") or ""
            )
            if not call_id:
                raise DatasetValidationError(
                    f"tool result at message {index} is missing tool_call_id"
                )
            if call_id in results:
                raise DatasetValidationError(
                    f"duplicate tool result for call id: {call_id}"
                )
            results[call_id] = index
    missing_results = sorted(set(calls) - set(results))
    orphan_results = sorted(set(results) - set(calls))
    if missing_results or orphan_results:
        raise DatasetValidationError(
            f"tool call/result mismatch: missing_results={missing_results}, "
            f"orphan_results={orphan_results}"
        )
    for call_id in calls:
        if results[call_id] <= calls[call_id]:
            raise DatasetValidationError(
                f"tool result precedes call for id: {call_id}"
            )


def _message_units(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    units: List[List[Dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        unit = [message]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            call_ids = {
                str(call.get("id"))
                for call in message.get("tool_calls") or []
                if call.get("id")
            }
            cursor = index + 1
            while cursor < len(messages):
                candidate = messages[cursor]
                if candidate.get("role") != "tool":
                    break
                call_id = str(
                    candidate.get("tool_call_id")
                    or candidate.get("call_id")
                    or ""
                )
                if call_id not in call_ids:
                    break
                unit.append(candidate)
                cursor += 1
            index = cursor
        else:
            index += 1
        units.append(unit)
    return units


def segment_messages(
    messages: List[Dict[str, Any]],
    *,
    max_messages: int,
) -> List[List[Dict[str, Any]]]:
    """Segment long chats without separating a tool call from its result."""
    if max_messages <= 0:
        raise ValueError("max_messages must be positive")
    validate_tool_alignment(messages)
    if len(messages) <= max_messages:
        return [copy.deepcopy(messages)]

    prefix = []
    for message in messages:
        if message.get("role") in {"system", "user"}:
            prefix.append(message)
        else:
            break
    units = _message_units(messages[len(prefix) :])
    segments: List[List[Dict[str, Any]]] = []
    current = copy.deepcopy(prefix)
    for unit in units:
        if len(current) > len(prefix) and len(current) + len(unit) > max_messages:
            segments.append(current)
            current = copy.deepcopy(prefix)
        current.extend(copy.deepcopy(unit))
    if current:
        segments.append(current)
    for segment in segments:
        validate_tool_alignment(segment)
    return segments


def to_sft_sample(
    messages: List[Dict[str, Any]],
    *,
    task_id: str,
    trajectory_id: str,
    segment_index: int,
) -> Dict[str, Any]:
    annotated = []
    for message in messages:
        item = copy.deepcopy(message)
        item["trainable"] = item.get("role") == "assistant"
        annotated.append(item)
    return {
        "messages": annotated,
        "loss_roles": ["assistant"],
        "metadata": {
            "task_id": task_id,
            "trajectory_id": trajectory_id,
            "segment_index": segment_index,
        },
    }
