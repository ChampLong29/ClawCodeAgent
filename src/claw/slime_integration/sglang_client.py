"""SGLang-based training client for on-policy RL.

Provides the same interface as OpenAICompatClient but additionally returns
token-level log-probabilities from the SGLang inference server, which are
required for PPO/GRPO policy gradient updates.

Usage in on-policy mode:
    client = SGLangTrainingClient(
        sglang_url="http://localhost:30000",
        model="Qwen/Qwen3-0.5B",
    )
    response = client.complete(messages, tools=tools)
    # response["_log_probs"] contains per-token log probs
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.error


class SGLangTrainingClient:
    """Model client that talks to SGLang server and returns log-probs.

    This client is injected into LocalCodingAgent during on-policy training
    to replace the default OpenAI/Anthropic client. The agent loop itself
    remains unchanged — it just gets richer response metadata.

    The log-probs are stored in response["_log_probs"] as a list of floats,
    one per generated token. The rollout function collects these across
    multiple turns and concatenates them into sample.rollout_log_probs.
    """

    def __init__(
        self,
        sglang_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 300,
    ):
        self.sglang_url = sglang_url or os.environ.get(
            "SGLANG_URL", "http://localhost:30000"
        )
        self.model = model or os.environ.get("SGLANG_MODEL", "")
        self._timeout = timeout

    def complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Make a completion request to SGLang with log-prob return.

        Returns the same format as OpenAICompatClient.complete() plus:
        - response["_log_probs"]: list[float] — per-token log probabilities
        - response["_token_ids"]: list[int] — generated token IDs
        """
        url = f"{self.sglang_url.rstrip('/')}/v1/chat/completions"

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "logprobs": True,  # Request token-level log probs
            "top_logprobs": 1,
        }

        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        else:
            payload["max_tokens"] = 4096
        if tools:
            payload["tools"] = tools

        headers = {
            "Content-Type": "application/json",
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})

                result: Dict[str, Any] = {
                    "role": message.get("role", "assistant"),
                    "content": message.get("content", ""),
                }

                # Handle tool_calls
                if "tool_calls" in message:
                    result["tool_calls"] = message["tool_calls"]

                # Extract log-probs from response
                logprobs_data = choice.get("logprobs", {})
                token_logprobs = []
                token_ids = []

                if logprobs_data and "content" in logprobs_data:
                    for token_info in logprobs_data["content"]:
                        if token_info and "logprob" in token_info:
                            token_logprobs.append(token_info["logprob"])
                        if token_info and "token" in token_info:
                            # Store token text for debugging; IDs not always available
                            pass

                result["_log_probs"] = token_logprobs
                result["_token_ids"] = token_ids

                # Parse usage
                usage = data.get("usage", {})
                result["usage"] = {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "model_calls": 1,
                    "tool_calls": len(message.get("tool_calls", [])),
                }

                return result

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(f"SGLang HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"SGLang connection error: {e.reason}")

    def stream(self, *args, **kwargs):
        """Streaming not supported in training mode — use complete()."""
        raise NotImplementedError(
            "SGLangTrainingClient does not support streaming. "
            "On-policy training uses non-streaming mode for log-prob collection."
        )


class TrajectoryRecorder:
    """Wrapper client that records trajectories for offline data collection.

    Wraps any existing model client (OpenAI/Anthropic) and additionally
    logs all request/response pairs to a JSONL file. Used in data_collection
    mode to produce training data without requiring SGLang.

    The recorded trajectories can later be replayed through SGLang to
    compute log-probs for off-policy RL training.
    """

    def __init__(self, inner_client: Any, output_path: str):
        self._inner = inner_client
        self._output_path = output_path
        self._turn_buffer: List[Dict[str, Any]] = []
        # Expose model attribute for agent_runtime compatibility
        self.model = getattr(inner_client, "model", "unknown")

    def complete(self, *args, **kwargs) -> Dict[str, Any]:
        """Forward to inner client and record the exchange."""
        response = self._inner.complete(*args, **kwargs)

        # Record this turn
        record = {
            "messages": args[0] if args else kwargs.get("messages", []),
            "response": response,
            "tools": kwargs.get("tools"),
        }
        self._turn_buffer.append(record)

        return response

    def stream(self, *args, **kwargs):
        """Forward streaming to inner client (no recording in stream mode)."""
        return self._inner.stream(*args, **kwargs)

    def flush_episode(self, episode_metadata: Optional[Dict[str, Any]] = None) -> None:
        """Write buffered turns to JSONL and clear buffer.

        Call this at the end of each episode (task completion).
        """
        if not self._turn_buffer:
            return

        episode = {
            "turns": self._turn_buffer,
            "metadata": episode_metadata or {},
        }

        with open(self._output_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(episode, ensure_ascii=False) + "\n")

        self._turn_buffer = []
