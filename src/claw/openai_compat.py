"""OpenAI-compatible model client."""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional, Union
import urllib.request
import urllib.error


class OpenAICompatError(Exception):
    """Error from OpenAI-compatible API."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _parse_tool_calls(response_content: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """Parse tool calls from response content."""
    if not response_content:
        return None
    try:
        # Try to parse as JSON
        data = json.loads(response_content)
        if isinstance(data, dict) and "tool_calls" in data:
            return data["tool_calls"]
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


class OpenAICompatClient:
    """OpenAI-compatible model client with streaming support."""

    # Configurable timeout (seconds) via CLAW_API_TIMEOUT env var
    DEFAULT_TIMEOUT = 300

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "local-token")
        self.model = model or os.environ.get("OPENAI_MODEL", "Qwen/Qwen3-Coder-30B-A3B-Instruct")
        self._timeout = int(os.environ.get("CLAW_API_TIMEOUT", str(self.DEFAULT_TIMEOUT)))

    def complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Make a non-streaming completion request."""
        url = f"{self.base_url.rstrip('/')}/chat/completions"

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
        }

        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
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

                # Parse response
                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})

                result = {
                    "role": message.get("role", "assistant"),
                    "content": message.get("content", ""),
                }

                # Handle tool_calls
                if "tool_calls" in message:
                    result["tool_calls"] = message["tool_calls"]

                # Handle legacy function_call
                if "function_call" in message:
                    result["function_call"] = message["function_call"]

                # Parse usage
                usage = data.get("usage", {})
                result["usage"] = {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "model_calls": 1,
                    "tool_calls": 0,
                }

                # Count tool calls if present
                if "tool_calls" in message:
                    result["usage"]["tool_calls"] = len(message["tool_calls"])

                return result

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            raise OpenAICompatError(f"HTTP {e.code}: {body}", status_code=e.code)
        except urllib.error.URLError as e:
            raise OpenAICompatError(f"Connection error: {e.reason}")

    def stream(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Make a streaming completion request."""
        url = f"{self.base_url.rstrip('/')}/chat/completions"

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
        }

        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
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
                # SSE streaming
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue
                    line = line[6:]
                    if line == "[DONE]":
                        break

                    try:
                        chunk = json.loads(line)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})

                        result = {"role": delta.get("role", "assistant")}
                        if "content" in delta:
                            result["content"] = delta["content"]
                        if "tool_calls" in delta:
                            result["tool_calls"] = delta["tool_calls"]
                        if "function_call" in delta:
                            result["function_call"] = delta["function_call"]

                        yield result
                    except json.JSONDecodeError:
                        continue

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            raise OpenAICompatError(f"HTTP {e.code}: {body}", status_code=e.code)
        except urllib.error.URLError as e:
            raise OpenAICompatError(f"Connection error: {e.reason}")


class AnthropicClient:
    """Anthropic-native API client.

    Handles proper Anthropic Messages API format including:
    - tool_use content blocks in assistant messages
    - tool_result content blocks in user messages
    - thinking content blocks (DeepSeek, Claude extended thinking)
    - Correct user/assistant message alternation

    Thinking support:
        Controlled by CLAW_THINKING_ENABLED env var (default: "auto").
        - "true"  — always send thinking config in requests
        - "false" — never send thinking config; strip thinking blocks from history
        - "auto"  — don't send thinking config but handle thinking blocks if returned

        CLAW_THINKING_BUDGET env var sets budget_tokens (default: 10000).
    """

    # Configurable timeout (seconds) via CLAW_API_TIMEOUT env var
    DEFAULT_TIMEOUT = 300

    # Thinking effort presets: name → budget_tokens
    THINKING_EFFORT_PRESETS = {
        "low": 4000,
        "medium": 10000,
        "high": 30000,
        "max": 60000,
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        thinking_enabled: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        thinking_effort: Optional[str] = None,
    ):
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-3-sonnet-20240229")
        self._timeout = int(os.environ.get("CLAW_API_TIMEOUT", str(self.DEFAULT_TIMEOUT)))

        # Thinking configuration
        self._thinking_enabled = (
            thinking_enabled or os.environ.get("CLAW_THINKING_ENABLED", "auto")
        ).lower().strip()

        # Resolve thinking budget: explicit budget > effort preset > default
        explicit_budget = thinking_budget or (
            int(os.environ["CLAW_THINKING_BUDGET"])
            if os.environ.get("CLAW_THINKING_BUDGET")
            else None
        )
        if explicit_budget:
            self._thinking_budget = explicit_budget
        else:
            effort = (
                thinking_effort or os.environ.get("CLAW_THINKING_EFFORT", "medium")
            ).lower().strip()
            self._thinking_budget = self.THINKING_EFFORT_PRESETS.get(effort, 10000)

    def _convert_messages(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
    ) -> tuple:
        """Convert internal message format to Anthropic Messages API format.

        Internal format uses OpenAI-style messages:
        - {"role": "assistant", "content": "...", "tool_calls": [...]}
        - {"role": "assistant", "content": "...", "_thinking": "...", "_thinking_signature": "..."}
        - {"role": "tool", "tool_call_id": "...", "content": "..."}

        Anthropic format requires:
        - Assistant: {"role": "assistant", "content": [{"type": "thinking", ...}, {"type": "text", ...}, {"type": "tool_use", ...}]}
        - Tool results: {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", ...}]}

        If thinking is disabled, thinking blocks are stripped from history.

        Returns:
            (anthropic_messages, system_content)
        """
        anthropic_messages = []
        system_content = system_prompt or ""
        include_thinking = self._thinking_enabled != "false"

        for msg in messages:
            role = msg.get("role", "user")

            if role == "system":
                system_content = msg.get("content", "")
                continue

            if role == "assistant":
                # Build proper content blocks for assistant messages
                content_blocks = []

                # Thinking block must come FIRST if present (Anthropic requirement)
                if include_thinking and msg.get("_thinking"):
                    thinking_block: Dict[str, Any] = {
                        "type": "thinking",
                        "thinking": msg["_thinking"],
                    }
                    if msg.get("_thinking_signature"):
                        thinking_block["signature"] = msg["_thinking_signature"]
                    content_blocks.append(thinking_block)

                text = msg.get("content", "")
                if text:
                    content_blocks.append({"type": "text", "text": text})

                # Convert tool_calls to tool_use blocks
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    func = tc.get("function", {})
                    args_raw = func.get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except (json.JSONDecodeError, TypeError):
                            args = {"_raw": args_raw}
                    else:
                        args = args_raw
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": args,
                    })

                # Anthropic requires non-empty content
                if not content_blocks:
                    content_blocks = [{"type": "text", "text": ""}]

                anthropic_messages.append({
                    "role": "assistant",
                    "content": content_blocks,
                })

            elif role == "tool":
                # Tool results must be wrapped as tool_result blocks inside a user message
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }

                # Merge consecutive tool results into a single user message
                if (anthropic_messages
                        and anthropic_messages[-1]["role"] == "user"
                        and isinstance(anthropic_messages[-1]["content"], list)):
                    # Check if the last user message contains only tool_result blocks
                    last_content = anthropic_messages[-1]["content"]
                    if last_content and last_content[0].get("type") == "tool_result":
                        last_content.append(tool_result_block)
                        continue

                anthropic_messages.append({
                    "role": "user",
                    "content": [tool_result_block],
                })

            else:
                # Regular user message
                content = msg.get("content", "")
                anthropic_messages.append({
                    "role": "user",
                    "content": content,
                })

        return anthropic_messages, system_content

    def complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Make a non-streaming completion request."""
        url = f"{self.base_url.rstrip('/')}/v1/messages"

        anthropic_messages, system_content = self._convert_messages(messages, system_prompt)

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": anthropic_messages,
        }

        if system_content:
            payload["system"] = system_content

        if temperature is not None:
            payload["temperature"] = temperature

        # Anthropic requires max_tokens
        payload["max_tokens"] = max_tokens or 4096

        if tools:
            payload["tools"] = tools

        # Add thinking configuration if explicitly enabled
        if self._thinking_enabled == "true":
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }
            # When thinking is enabled, temperature must be 1 (Anthropic requirement)
            if "temperature" in payload:
                del payload["temperature"]

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
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

                # Parse Anthropic response
                content_blocks = data.get("content", [])
                content_text = ""
                tool_calls = []
                thinking_text = ""
                thinking_signature = ""

                for block in content_blocks:
                    block_type = block.get("type", "")
                    if block_type == "text":
                        content_text += block.get("text", "")
                    elif block_type == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            }
                        })
                    elif block_type == "thinking":
                        # Capture thinking content for session history replay
                        thinking_text += block.get("thinking", "") or block.get("text", "")
                        if block.get("signature"):
                            thinking_signature = block["signature"]
                        import sys
                        chars = len(thinking_text)
                        print(f"  \033[90m💭 thinking ({chars} chars)\033[0m", file=sys.stderr)

                result = {
                    "role": "assistant",
                    "content": content_text,
                }

                if tool_calls:
                    result["tool_calls"] = tool_calls

                # Attach thinking metadata so caller can persist it in session
                if thinking_text:
                    result["_thinking"] = thinking_text
                if thinking_signature:
                    result["_thinking_signature"] = thinking_signature

                # Parse usage (Anthropic format)
                usage = data.get("usage", {})
                result["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "model_calls": 1,
                    "tool_calls": len(tool_calls),
                }
                # Track thinking tokens separately if available
                if usage.get("cache_creation_input_tokens"):
                    result["usage"]["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]
                if usage.get("cache_read_input_tokens"):
                    result["usage"]["cache_read_input_tokens"] = usage["cache_read_input_tokens"]

                return result

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            raise OpenAICompatError(f"HTTP {e.code}: {body}", status_code=e.code)
        except urllib.error.URLError as e:
            raise OpenAICompatError(f"Connection error: {e.reason}")

    def stream(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Make a streaming completion request."""
        url = f"{self.base_url.rstrip('/')}/v1/messages"

        anthropic_messages, system_content = self._convert_messages(messages, system_prompt)

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": anthropic_messages,
            "stream": True,
        }

        if system_content:
            payload["system"] = system_content

        if temperature is not None:
            payload["temperature"] = temperature

        payload["max_tokens"] = max_tokens or 4096

        if tools:
            payload["tools"] = tools

        # Add thinking configuration if explicitly enabled
        if self._thinking_enabled == "true":
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }
            if "temperature" in payload:
                del payload["temperature"]

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                # Track thinking state across stream chunks
                _thinking_buffer = []
                _thinking_signature = ""
                _in_thinking_block = False

                for line in resp:
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        # SSE event type line — skip, we parse data lines
                        continue
                    if line.startswith("data: "):
                        line = line[6:]

                    if line == "[DONE]":
                        break

                    try:
                        chunk = json.loads(line)
                        event_type = chunk.get("type", "")

                        if event_type == "content_block_start":
                            block = chunk.get("content_block", {})
                            block_type = block.get("type", "")
                            if block_type == "text":
                                _in_thinking_block = False
                                yield {"role": "assistant", "content": ""}
                            elif block_type == "tool_use":
                                _in_thinking_block = False
                                yield {"role": "assistant", "tool_call": {
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                }}
                            elif block_type == "thinking":
                                _in_thinking_block = True
                                import sys
                                print(f"  \033[90m💭 thinking...\033[0m", file=sys.stderr, flush=True)

                        elif event_type == "content_block_delta":
                            delta = chunk.get("delta", {})
                            delta_type = delta.get("type", "")
                            if delta_type == "text_delta":
                                yield {"role": "assistant", "content": delta.get("text", "")}
                            elif delta_type == "input_json_delta":
                                yield {"role": "assistant", "partial_args": delta.get("partial_json", "")}
                            elif delta_type == "thinking_delta":
                                # Accumulate thinking content for session persistence
                                _thinking_buffer.append(delta.get("thinking", ""))
                            elif delta_type == "signature_delta":
                                _thinking_signature += delta.get("signature", "")

                        elif event_type == "content_block_stop":
                            _in_thinking_block = False

                        elif event_type == "message_delta":
                            # End of message — emit accumulated thinking as metadata
                            if _thinking_buffer:
                                thinking_text = "".join(_thinking_buffer)
                                yield {
                                    "role": "assistant",
                                    "_thinking": thinking_text,
                                    "_thinking_signature": _thinking_signature or None,
                                }
                        elif event_type == "error":
                            error_data = chunk.get("error", {})
                            error_msg = error_data.get("message", "Unknown streaming error")
                            raise OpenAICompatError(f"Stream error: {error_msg}")
                    except json.JSONDecodeError:
                        continue

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            raise OpenAICompatError(f"HTTP {e.code}: {body}", status_code=e.code)
        except urllib.error.URLError as e:
            raise OpenAICompatError(f"Connection error: {e.reason}")