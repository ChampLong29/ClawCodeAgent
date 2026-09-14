#!/usr/bin/env python3
"""Probe OpenAI-compatible multi-turn tool and reasoning replay semantics."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from claw.api_config import APIConfigRuntime
from claw.openai_compat import OpenAICompatClient


def _single_call(response: dict, expected_name: str) -> dict:
    calls = response.get("tool_calls") or []
    if len(calls) != 1:
        raise RuntimeError(
            f"expected one {expected_name} tool call, received {len(calls)}"
        )
    call = calls[0]
    function = call.get("function") or {}
    if function.get("name") != expected_name:
        raise RuntimeError(
            f"expected tool {expected_name}, received {function.get('name')!r}"
        )
    raw = function.get("arguments", "{}")
    arguments = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(arguments, dict):
        raise RuntimeError("tool arguments are not an object")
    return {"call": call, "arguments": arguments}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a two-turn tool round trip without sending repository content."
        )
    )
    parser.add_argument("--api-config-root", type=Path, default=Path.cwd())
    parser.add_argument("--model")
    parser.add_argument(
        "--thinking-mode", choices=("enabled", "disabled"), default="disabled"
    )
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.max_tokens <= 0:
        raise SystemExit("--max-tokens must be positive")

    config = APIConfigRuntime(str(args.api_config_root.resolve())).get_config()
    base_url = config.base_url.rstrip("/")
    if base_url.endswith("/anthropic"):
        base_url = base_url[: -len("/anthropic")]
    model = args.model or config.model
    client = OpenAICompatClient(
        base_url=base_url,
        api_key=config.api_key,
        model=model,
    )
    tools = [{
        "type": "function",
        "function": {
            "name": "inspect_target",
            "description": "Read one synthetic source path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }]
    messages = [{
        "role": "user",
        "content": (
            "Call inspect_target exactly once with path src/example.py. "
            "Do not explain."
        ),
    }]
    first = client.complete(
        messages=messages,
        tools=tools,
        temperature=0.0,
        max_tokens=args.max_tokens,
        thinking_mode=args.thinking_mode,
    )
    call = _single_call(first, "inspect_target")
    second = client.complete(
        messages=messages + [
            first,
            {
                "role": "tool",
                "tool_call_id": call["call"].get("id", ""),
                "content": "VALUE = 0\n",
            },
            {
                "role": "user",
                "content": "Reply with exactly: protocol-ok. Do not call a tool.",
            },
        ],
        tools=tools,
        temperature=0.0,
        max_tokens=args.max_tokens,
        thinking_mode=args.thinking_mode,
    )
    reasoning = first.get("reasoning_content")
    checks = {
        "first_tool_name": True,
        "first_path": call["arguments"].get("path") == "src/example.py",
        "reasoning_present_when_enabled": (
            bool(reasoning) if args.thinking_mode == "enabled" else True
        ),
        "second_turn_completed": second.get("finish_reason") not in {
            "length", "max_tokens"
        },
        "second_text_present": bool(str(second.get("content") or "").strip()),
    }
    parsed = urlparse(base_url)
    payload = {
        "schema_version": "openai_tool_reasoning_probe.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": {"host": parsed.hostname, "path": parsed.path},
        "requested_model": model,
        "thinking_mode": args.thinking_mode,
        "max_tokens_per_request": args.max_tokens,
        "checks": checks,
        "verified": all(checks.values()),
        "responses": [
            {
                "finish_reason": first.get("finish_reason"),
                "usage": first.get("usage", {}),
                "reasoning_chars": len(str(reasoning or "")),
                "tool_name": "inspect_target",
                "arguments": call["arguments"],
            },
            {
                "finish_reason": second.get("finish_reason"),
                "usage": second.get("usage", {}),
                "content_chars": len(str(second.get("content") or "")),
            },
        ],
        "claim_boundary": (
            "Synthetic provider-contract evidence only; no repository content was "
            "sent and task quality was not evaluated."
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
