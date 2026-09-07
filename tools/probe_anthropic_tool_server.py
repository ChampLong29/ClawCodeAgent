#!/usr/bin/env python3
"""Probe an Anthropic-compatible provider's structured multi-turn tool contract."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from claw.api_config import APIConfigRuntime, APIProvider
from claw.openai_compat import AnthropicClient


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def _single_tool_call(response: dict, expected_name: str) -> dict:
    calls = response.get("tool_calls") or []
    if len(calls) != 1:
        raise RuntimeError(
            f"expected one {expected_name} tool call, received {len(calls)}"
        )
    call = calls[0]
    actual_name = str((call.get("function") or {}).get("name", ""))
    if actual_name != expected_name:
        raise RuntimeError(
            f"expected tool {expected_name}, received {actual_name or '<empty>'}"
        )
    raw_arguments = (call.get("function") or {}).get("arguments", "{}")
    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    if not isinstance(arguments, dict):
        raise RuntimeError(f"{expected_name} arguments are not an object")
    return {"call": call, "arguments": arguments}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify required tool calls and a tool-result round trip against an "
            "Anthropic-compatible endpoint without sending repository content."
        )
    )
    parser.add_argument("--api-config-root", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--thinking-mode",
        choices=("auto", "enabled", "disabled"),
        default="disabled",
    )
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    config = APIConfigRuntime(str(args.api_config_root.resolve())).get_config()
    if config.provider != APIProvider.ANTHROPIC:
        raise SystemExit(
            "configured provider is not anthropic; use an isolated Anthropic profile"
        )
    if args.max_tokens <= 0:
        raise SystemExit("--max-tokens must be positive")

    model = args.model or config.model
    client = AnthropicClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=model,
    )
    inspect_tool = _tool(
        "inspect_target",
        "Select one repository-relative source path for inspection.",
        {"path": {"type": "string"}},
        ["path"],
    )
    patch_tool = _tool(
        "propose_patch",
        "Propose one exact replacement after inspection.",
        {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        ["path", "old_string", "new_string"],
    )

    first_messages = [{
        "role": "user",
        "content": (
            "Call inspect_target exactly once with path src/example.py. "
            "Do not return explanatory text."
        ),
    }]
    first = client.complete(
        messages=first_messages,
        tools=[inspect_tool],
        tool_choice="required",
        temperature=0.0,
        max_tokens=args.max_tokens,
        thinking_mode=args.thinking_mode,
    )
    first_call = _single_tool_call(first, "inspect_target")

    second_messages = first_messages + [
        {
            "role": "assistant",
            "content": first.get("content", ""),
            "tool_calls": first.get("tool_calls", []),
        },
        {
            "role": "tool",
            "tool_call_id": first_call["call"].get("id", ""),
            "content": "VALUE = 0\n",
        },
        {
            "role": "user",
            "content": (
                "Call propose_patch exactly once for src/example.py, replacing "
                "VALUE = 0 with VALUE = 1. Do not return explanatory text."
            ),
        },
    ]
    second = client.complete(
        messages=second_messages,
        tools=[patch_tool],
        tool_choice="required",
        temperature=0.0,
        max_tokens=args.max_tokens,
        thinking_mode=args.thinking_mode,
    )
    second_call = _single_tool_call(second, "propose_patch")

    parsed = urlparse(config.base_url)
    first_metadata = first.get("_provider_metadata") or {}
    second_metadata = second.get("_provider_metadata") or {}
    checks = {
        "first_tool_name": True,
        "first_path": first_call["arguments"].get("path") == "src/example.py",
        "second_tool_name": True,
        "second_path": second_call["arguments"].get("path") == "src/example.py",
        "second_old_string": "VALUE = 0" in str(
            second_call["arguments"].get("old_string", "")
        ),
        "second_new_string": "VALUE = 1" in str(
            second_call["arguments"].get("new_string", "")
        ),
        "response_ids_present": bool(
            first_metadata.get("response_id") and second_metadata.get("response_id")
        ),
        "response_models_present": bool(
            first_metadata.get("response_model")
            and second_metadata.get("response_model")
        ),
    }
    payload = {
        "schema_version": "anthropic_tool_provider_probe.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "anthropic",
        "endpoint": {"host": parsed.hostname, "path": parsed.path},
        "requested_model": model,
        "thinking_mode": args.thinking_mode,
        "max_tokens_per_request": args.max_tokens,
        "checks": checks,
        "verified": all(checks.values()),
        "responses": [
            {
                "finish_reason": first.get("finish_reason"),
                "provider_metadata": first_metadata,
                "usage": first.get("usage", {}),
                "tool_name": "inspect_target",
                "arguments": first_call["arguments"],
            },
            {
                "finish_reason": second.get("finish_reason"),
                "provider_metadata": second_metadata,
                "usage": second.get("usage", {}),
                "tool_name": "propose_patch",
                "arguments": second_call["arguments"],
            },
        ],
        "claim_boundary": (
            "Synthetic provider-contract evidence only; no repository content was sent "
            "and no task-quality capability was evaluated."
        ),
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
