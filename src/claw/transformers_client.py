"""In-process Transformers model client with Qwen-style tool calling."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


_TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL
)
_THINK_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def parse_qwen_tool_response(text: str) -> tuple[str, List[Dict[str, Any]]]:
    """Convert Qwen XML tool calls into the runtime's OpenAI-style schema."""
    calls: List[Dict[str, Any]] = []
    for raw in _TOOL_CALL_PATTERN.findall(text):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        name = payload.get("name")
        arguments = payload.get("arguments", {})
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(arguments, dict):
            continue
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:20]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
    content = _TOOL_CALL_PATTERN.sub("", text)
    content = _THINK_PATTERN.sub("", content).strip()
    return content, calls


class TransformersToolClient:
    """Lazy-loading local Hugging Face/ModelScope snapshot client.

    The public ``complete`` method matches the subset used by
    :class:`LocalCodingAgent`. Streaming is intentionally unsupported: benchmark
    Episodes use deterministic non-streaming generation and preserve the raw
    model/tool boundary in Trajectory v2.
    """

    def __init__(
        self,
        model_path: Union[str, Path],
        *,
        model_name: Optional[str] = None,
        device: str = "cuda:0",
        max_context_tokens: int = 32768,
        default_max_new_tokens: int = 1024,
        enable_thinking: bool = False,
    ):
        self.model_path = str(Path(model_path).expanduser().resolve())
        self.model = model_name or f"local:{Path(self.model_path).name}"
        self.device = device
        self.max_context_tokens = int(max_context_tokens)
        self.default_max_new_tokens = int(default_max_new_tokens)
        self.enable_thinking = bool(enable_thinking)
        if self.max_context_tokens <= 0 or self.default_max_new_tokens <= 0:
            raise ValueError("local model token limits must be positive")
        if not Path(self.model_path).is_dir():
            raise FileNotFoundError(self.model_path)
        self._tokenizer = None
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "local Transformers inference requires torch and transformers"
            ) from exc
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")
        tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, local_files_only=True, trust_remote_code=False
        )
        tokenizer.truncation_side = "left"
        model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            local_files_only=True,
            trust_remote_code=False,
            torch_dtype="auto",
            device_map={"": self.device},
        )
        model.eval()
        self._tokenizer = tokenizer
        self._model = model

    def complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        thinking_mode: Optional[str] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        if stream:
            raise ValueError("TransformersToolClient supports non-streaming runs only")
        self._load()
        assert self._tokenizer is not None and self._model is not None
        import torch

        output_budget = int(max_tokens or self.default_max_new_tokens)
        if output_budget <= 0 or output_budget >= self.max_context_tokens:
            raise ValueError("invalid local model output token budget")
        thinking = self.enable_thinking
        if thinking_mode == "disabled":
            thinking = False
        elif thinking_mode == "enabled":
            thinking = True
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tools=tools or None,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=thinking,
        )
        encoded = self._tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_context_tokens - output_budget,
        )
        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        input_tokens = int(encoded["input_ids"].shape[-1])
        sampling = temperature is not None and float(temperature) > 0
        generation_kwargs: Dict[str, Any] = {
            **encoded,
            "max_new_tokens": output_budget,
            "do_sample": sampling,
            "pad_token_id": self._tokenizer.eos_token_id,
        }
        if sampling:
            generation_kwargs["temperature"] = float(temperature)
        with torch.inference_mode():
            generated = self._model.generate(**generation_kwargs)
        new_tokens = generated[0, input_tokens:]
        output_tokens = int(new_tokens.shape[-1])
        raw_text = self._tokenizer.decode(new_tokens, skip_special_tokens=True)
        content, tool_calls = parse_qwen_tool_response(raw_text)
        result: Dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "finish_reason": (
                "tool_calls"
                if tool_calls
                else ("length" if output_tokens >= output_budget else "stop")
            ),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model_calls": 1,
                "tool_calls": len(tool_calls),
            },
        }
        if tool_calls:
            result["tool_calls"] = tool_calls
        return result

    def stream(self, *args: Any, **kwargs: Any):
        raise ValueError("TransformersToolClient supports non-streaming runs only")
