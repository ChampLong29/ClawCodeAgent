"""Explicit tool-use chat rendering and assistant-only loss labels."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from .base import TrainingBackendError


SUPPORTED_ROLES = {"system", "user", "assistant", "tool"}
IGNORE_INDEX = -100


def _content(message: Dict[str, Any]) -> str:
    value = message.get("content", "")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass
class EncodedChat:
    input_ids: List[int]
    attention_mask: List[int]
    labels: List[int]

    @property
    def trainable_token_count(self) -> int:
        return sum(label != IGNORE_INDEX for label in self.labels)


class ToolUseChatEncoder:
    """Encode all four roles while masking everything except assistant output."""

    def __init__(self, tokenizer: Any, *, max_length: int = 4096):
        if max_length <= 0:
            raise ValueError("max_length must be positive")
        if not hasattr(tokenizer, "encode"):
            raise TypeError("tokenizer must expose encode(text, add_special_tokens=...)")
        self.tokenizer = tokenizer
        self.max_length = max_length

    @staticmethod
    def render_message(message: Dict[str, Any]) -> str:
        role = str(message.get("role") or "")
        if role not in SUPPORTED_ROLES:
            raise TrainingBackendError(f"unsupported chat role: {role!r}")
        parts = [f"<|{role}|>\n", _content(message)]
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                parts.extend(
                    [
                        "\n<tool_call>",
                        json.dumps(
                            call,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        "</tool_call>",
                    ]
                )
        elif role == "tool":
            call_id = str(
                message.get("tool_call_id") or message.get("call_id") or ""
            )
            if call_id:
                parts.extend(["\n<tool_result_id>", call_id, "</tool_result_id>"])
        parts.append("\n<|end|>\n")
        return "".join(parts)

    @staticmethod
    def _message_units(
        messages: Sequence[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
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
                    candidate_id = str(
                        candidate.get("tool_call_id")
                        or candidate.get("call_id")
                        or ""
                    )
                    if candidate_id not in call_ids:
                        break
                    unit.append(candidate)
                    cursor += 1
                result_ids = {
                    str(
                        candidate.get("tool_call_id")
                        or candidate.get("call_id")
                        or ""
                    )
                    for candidate in unit[1:]
                }
                if result_ids != call_ids:
                    raise TrainingBackendError(
                        "assistant tool calls do not have complete adjacent results"
                    )
                index = cursor
            else:
                index += 1
            units.append(unit)
        return units

    def encode(self, messages: Sequence[Dict[str, Any]]) -> EncodedChat:
        if not messages:
            raise TrainingBackendError("training sample contains no messages")
        input_ids: List[int] = []
        labels: List[int] = []
        bos_token_id = getattr(self.tokenizer, "bos_token_id", None)
        if bos_token_id is not None:
            input_ids.append(int(bos_token_id))
            labels.append(IGNORE_INDEX)
        eos_token_id = getattr(self.tokenizer, "eos_token_id", None)
        reserved_eos = 1 if eos_token_id is not None else 0
        last_role = ""

        for unit in self._message_units(messages):
            unit_ids: List[int] = []
            unit_labels: List[int] = []
            for message in unit:
                token_ids = list(
                    self.tokenizer.encode(
                        self.render_message(message), add_special_tokens=False
                    )
                )
                trainable = (
                    message.get("role") == "assistant"
                    and message.get("trainable", True) is not False
                )
                unit_ids.extend(int(token_id) for token_id in token_ids)
                unit_labels.extend(
                    [int(token_id) for token_id in token_ids]
                    if trainable
                    else [IGNORE_INDEX] * len(token_ids)
                )
            if len(input_ids) + len(unit_ids) + reserved_eos > self.max_length:
                break
            input_ids.extend(unit_ids)
            labels.extend(unit_labels)
            last_role = str(unit[-1].get("role") or "")

        if eos_token_id is not None and len(input_ids) < self.max_length:
            input_ids.append(int(eos_token_id))
            labels.append(
                int(eos_token_id) if last_role == "assistant" else IGNORE_INDEX
            )
        if not any(label != IGNORE_INDEX for label in labels):
            raise TrainingBackendError(
                "sample has no complete assistant unit within max_length"
            )
        return EncodedChat(
            input_ids=input_ids,
            attention_mask=[1] * len(input_ids),
            labels=labels,
        )

    def encode_samples(
        self, samples: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, List[int]]]:
        encoded = []
        for index, sample in enumerate(samples):
            messages = sample.get("messages")
            if not isinstance(messages, list):
                raise TrainingBackendError(
                    f"sample {index} is missing a messages list"
                )
            chat = self.encode(messages)
            encoded.append(
                {
                    "input_ids": chat.input_ids,
                    "attention_mask": chat.attention_mask,
                    "labels": chat.labels,
                }
            )
        return encoded


class AssistantOnlyDataCollator:
    """Pad already-masked samples for Transformers Trainer."""

    def __init__(self, tokenizer: Any):
        self.tokenizer = tokenizer

    def __call__(self, features: Sequence[Dict[str, List[int]]]) -> Dict[str, Any]:
        try:
            import torch
        except ImportError as exc:
            raise TrainingBackendError("PyTorch is required for collation") from exc
        if not features:
            raise TrainingBackendError("cannot collate an empty batch")
        max_length = max(len(item["input_ids"]) for item in features)
        pad_token_id = getattr(self.tokenizer, "pad_token_id", None)
        if pad_token_id is None:
            pad_token_id = getattr(self.tokenizer, "eos_token_id", 0)
        input_ids = []
        attention_mask = []
        labels = []
        for item in features:
            padding = max_length - len(item["input_ids"])
            input_ids.append(item["input_ids"] + [int(pad_token_id)] * padding)
            attention_mask.append(item["attention_mask"] + [0] * padding)
            labels.append(item["labels"] + [IGNORE_INDEX] * padding)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(attention_mask, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
        }
