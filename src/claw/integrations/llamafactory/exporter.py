"""Export Silver records to LLaMAFactory's ShareGPT tool-use format."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from ...agent_tools import default_tool_registry
from ...data_pipeline.manifest import SilverDatasetManifest
from ...data_pipeline.schemas import AgentTrainingRecord
from ...experiment.schemas import canonical_hash
from ...trajectory.schema import stable_id


LLAMAFACTORY_MIN_VERSION = "0.9.4"
LLAMAFACTORY_EXPORT_SCHEMA_VERSION = "llamafactory_export_manifest.v1"


class LlamaFactoryExportError(ValueError):
    """Raised when a Silver record cannot be represented safely."""


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def default_tool_schemas() -> List[Dict[str, Any]]:
    """Return stable schemas for Claw's built-in tools without external deps."""
    return sorted(
        (tool.to_dict() for tool in default_tool_registry().list_tools()),
        key=lambda item: item["name"],
    )


def _call_parts(call: Dict[str, Any]) -> tuple[str, Any]:
    function = call.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        arguments = function.get("arguments", {})
    else:
        name = call.get("name")
        arguments = call.get("arguments", {})
    if not isinstance(name, str) or not name.strip():
        raise LlamaFactoryExportError("tool call is missing a name")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise LlamaFactoryExportError(
                f"tool call {name!r} has invalid JSON arguments"
            ) from exc
    if not isinstance(arguments, dict):
        raise LlamaFactoryExportError(
            f"tool call {name!r} arguments must be an object"
        )
    return name, arguments


def _used_tool_names(record: AgentTrainingRecord) -> Set[str]:
    names: Set[str] = set()
    for message in record.messages:
        for call in message.get("tool_calls") or []:
            name, _ = _call_parts(call)
            names.add(name)
    return names


def _schema_index(
    tool_schemas: Iterable[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for schema in tool_schemas:
        if not isinstance(schema, dict):
            raise LlamaFactoryExportError("tool schema must be an object")
        function = schema.get("function")
        normalized = function if isinstance(function, dict) else schema
        name = normalized.get("name")
        if not isinstance(name, str) or not name.strip():
            raise LlamaFactoryExportError("tool schema is missing a name")
        if name in index:
            raise LlamaFactoryExportError(f"duplicate tool schema: {name}")
        index[name] = copy.deepcopy(normalized)
    return index


def to_sharegpt_sample(
    record: AgentTrainingRecord,
    *,
    tool_schemas: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Map OpenAI-style messages to ShareGPT function_call/observation roles."""
    record.validate(for_training=True)
    schema_by_name = _schema_index(tool_schemas)
    used_names = _used_tool_names(record)
    missing = sorted(used_names - set(schema_by_name))
    if missing:
        raise LlamaFactoryExportError(f"missing schemas for tools: {missing}")

    conversations: List[Dict[str, str]] = []
    pending_observations: List[str] = []

    def flush_observations() -> None:
        if not pending_observations:
            return
        conversations.append(
            {
                "from": "observation",
                "value": "\n</tool_response>\n<tool_response>\n".join(
                    pending_observations
                ),
            }
        )
        pending_observations.clear()

    for index, message in enumerate(record.messages):
        role = message.get("role")
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, sort_keys=True)
        if role == "tool":
            pending_observations.append(content)
            continue

        flush_observations()
        if role == "system":
            if conversations:
                raise LlamaFactoryExportError(
                    "system message must be the first conversation item"
                )
            conversations.append({"from": "system", "value": content})
        elif role == "user":
            conversations.append({"from": "human", "value": content})
        elif role == "assistant":
            calls = message.get("tool_calls") or []
            functions = []
            for call in calls:
                name, arguments = _call_parts(call)
                functions.append({"name": name, "arguments": arguments})
            if functions:
                function_value = json.dumps(
                    functions, ensure_ascii=False, sort_keys=True
                )
                if content:
                    function_value = (
                        f"<think>\n{content}\n</think>\n\n{function_value}"
                    )
                conversations.append(
                    {
                        "from": "function_call",
                        "value": function_value,
                    }
                )
            elif content:
                conversations.append({"from": "gpt", "value": content})
            else:
                raise LlamaFactoryExportError(
                    f"assistant message {index} has no content or tool call"
                )
        else:
            raise LlamaFactoryExportError(
                f"unsupported message role at index {index}: {role!r}"
            )
    flush_observations()

    dialogue = conversations[1:] if conversations and conversations[0]["from"] == "system" else conversations
    expected = (
        {"human", "observation"},
        {"gpt", "function_call"},
    )
    for turn_index, item in enumerate(dialogue):
        if item["from"] not in expected[turn_index % 2]:
            raise LlamaFactoryExportError(
                "ShareGPT roles do not alternate for LlamaFactory: "
                f"index={turn_index}, role={item['from']}"
            )
    if len(dialogue) % 2 != 0:
        raise LlamaFactoryExportError(
            "ShareGPT SFT conversation must end with an assistant/function turn"
        )
    selected_schemas = [schema_by_name[name] for name in sorted(used_names)]
    return {
        "conversations": conversations,
        "tools": json.dumps(
            selected_schemas, ensure_ascii=False, sort_keys=True
        ),
        "metadata": {
            "record_id": record.record_id,
            "task_id": record.task["task_id"],
            "trajectory_id": record.lineage["trajectory_ref"],
            "quality_score": record.features.get("quality_score"),
            "sample_weight": record.features.get("sample_weight"),
        },
    }


@dataclass
class LlamaFactoryExportResult:
    dataset_path: Path
    dataset_info_path: Path
    manifest_path: Path
    sample_count: int
    content_hash: str


class LlamaFactoryExporter:
    """Write self-contained ShareGPT data plus dataset_info and lineage."""

    def __init__(
        self,
        *,
        tool_schemas: Optional[Iterable[Dict[str, Any]]] = None,
    ):
        self.tool_schemas = list(
            default_tool_schemas() if tool_schemas is None else tool_schemas
        )

    def export(
        self,
        records: Iterable[AgentTrainingRecord],
        *,
        output_dir: Union[str, os.PathLike[str]],
        dataset_name: str = "claw_agent_sft",
        source_manifest: Optional[SilverDatasetManifest] = None,
    ) -> LlamaFactoryExportResult:
        if not dataset_name.strip():
            raise ValueError("dataset_name must not be empty")
        ordered = sorted(records, key=lambda item: item.record_id)
        record_ids = [item.record_id for item in ordered]
        if len(record_ids) != len(set(record_ids)):
            raise LlamaFactoryExportError("duplicate record_id in export")
        samples = [
            to_sharegpt_sample(item, tool_schemas=self.tool_schemas)
            for item in ordered
        ]
        content_hash = canonical_hash(samples)
        export_id = stable_id(
            "lf_export",
            {
                "record_ids": record_ids,
                "content_hash": content_hash,
                "source_manifest": (
                    source_manifest.dataset_id if source_manifest else None
                ),
            },
        )
        output = Path(output_dir).resolve()
        dataset_file = f"{dataset_name}.json"
        dataset_path = output / dataset_file
        dataset_info_path = output / "dataset_info.json"
        manifest_path = output / "llamafactory-export-manifest.json"
        dataset_info = {
            dataset_name: {
                "file_name": dataset_file,
                "formatting": "sharegpt",
                "columns": {
                    "messages": "conversations",
                    "tools": "tools",
                },
            }
        }
        manifest = {
            "schema_version": LLAMAFACTORY_EXPORT_SCHEMA_VERSION,
            "export_id": export_id,
            "dataset_name": dataset_name,
            "sample_count": len(samples),
            "record_ids": record_ids,
            "content_hash": content_hash,
            "source_manifest_id": (
                source_manifest.dataset_id if source_manifest else None
            ),
            "llamafactory_min_version": LLAMAFACTORY_MIN_VERSION,
            "format": "sharegpt_tool_use",
            "output_files": [dataset_file, "dataset_info.json"],
        }
        _atomic_write(
            dataset_path,
            json.dumps(samples, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        _atomic_write(
            dataset_info_path,
            json.dumps(dataset_info, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        _atomic_write(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        return LlamaFactoryExportResult(
            dataset_path=dataset_path,
            dataset_info_path=dataset_info_path,
            manifest_path=manifest_path,
            sample_count=len(samples),
            content_hash=content_hash,
        )
