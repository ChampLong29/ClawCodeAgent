"""Anthropic API compatibility layer for CodeAgent.

**DEPRECATED / LEGACY WRAPPER**

This module is the legacy Anthropic compatibility wrapper. It wraps an
OpenAI-compatible client under the hood, which limits native Anthropic features.

For new code, use the AnthropicClient in openai_compat.py directly:
    from src.openai_compat import AnthropicClient
    client = AnthropicClient(base_url=..., api_key=..., model=...)

The AnthropicClient provides:
- Native Anthropic Messages API support
- Proper system prompt handling (separate from messages)
- Content block-based tool use format
- Anthropic streaming (content_block_start/delta events)
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


class AnthropicCompatClient:
    """Client that provides Anthropic API compatibility.

    Wraps OpenAI-compatible client with format conversion.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        # Import here to avoid circular dependency
        from .openai_compat import OpenAICompatClient

        self._client = OpenAICompatClient(
            base_url=base_url or os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8000/v1"),
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", "local-token"),
            model=model or os.environ.get("ANTHROPIC_MODEL", "claude-3-sonnet-20240229"),
        )

    def complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Complete with Anthropic-style API.

        Args:
            messages: List of messages with 'role' and 'content'
            model: Model name
            max_tokens: Maximum output tokens
            temperature: Sampling temperature
            system_prompt: System prompt (will be prepended as system message)
        """
        # Convert Anthropic format to OpenAI format
        openai_messages = []

        # Add system prompt if provided
        if system_prompt:
            openai_messages.append({"role": "system", "content": system_prompt})

        # Convert messages
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Map user to user, assistant to assistant
            if role in ("user", "assistant", "system"):
                openai_messages.append({"role": role, "content": content})
            else:
                openai_messages.append({"role": "user", "content": content})

        return self._client.complete(
            messages=openai_messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def stream(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ):
        """Stream completions with Anthropic-style events."""
        openai_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant", "system"):
                openai_messages.append({"role": role, "content": content})
            else:
                openai_messages.append({"role": "user", "content": content})

        for chunk in self._client.stream(
            messages=openai_messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            yield chunk