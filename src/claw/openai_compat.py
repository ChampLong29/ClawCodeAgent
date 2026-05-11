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

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "local-token")
        self.model = model or os.environ.get("OPENAI_MODEL", "Qwen/Qwen3-Coder-30B-A3B-Instruct")

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
            with urllib.request.urlopen(req, timeout=120) as resp:
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
            with urllib.request.urlopen(req, timeout=120) as resp:
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
    """Anthropic-native API client."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-3-sonnet-20240229")

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

        # Convert messages to Anthropic format
        anthropic_messages = []
        system_content = system_prompt or ""

        for msg in messages:
            if msg.get("role") == "system":
                system_content = msg.get("content", "")
                continue
            role = msg.get("role", "user")
            if role == "assistant":
                role = "assistant"
            elif role == "tool":
                role = "user"  # Anthropic uses role=user for tool results
            else:
                role = "user"
            anthropic_messages.append({
                "role": role,
                "content": msg.get("content", ""),
            })

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
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))

                # Parse Anthropic response
                content_blocks = data.get("content", [])
                content_text = ""
                tool_calls = []

                for block in content_blocks:
                    if block.get("type") == "text":
                        content_text += block.get("text", "")
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            }
                        })

                result = {
                    "role": "assistant",
                    "content": content_text,
                }

                if tool_calls:
                    result["tool_calls"] = tool_calls

                # Parse usage (Anthropic format)
                usage = data.get("usage", {})
                result["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "model_calls": 1,
                    "tool_calls": len(tool_calls),
                }

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

        # Convert messages to Anthropic format
        anthropic_messages = []
        system_content = system_prompt or ""

        for msg in messages:
            if msg.get("role") == "system":
                system_content = msg.get("content", "")
                continue
            role = msg.get("role", "user")
            if role == "tool":
                role = "user"
            anthropic_messages.append({
                "role": role,
                "content": msg.get("content", ""),
            })

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
            with urllib.request.urlopen(req, timeout=120) as resp:
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if not line:
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
                            if block.get("type") == "text":
                                yield {"role": "assistant", "content": ""}
                            elif block.get("type") == "tool_use":
                                yield {"role": "assistant", "tool_call": {
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                }}
                        elif event_type == "content_block_delta":
                            delta = chunk.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield {"role": "assistant", "content": delta.get("text", "")}
                            elif delta.get("type") == "input_json_delta":
                                yield {"role": "assistant", "partial_args": delta.get("partial_json", "")}
                    except json.JSONDecodeError:
                        continue

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            raise OpenAICompatError(f"HTTP {e.code}: {body}", status_code=e.code)
        except urllib.error.URLError as e:
            raise OpenAICompatError(f"Connection error: {e.reason}")