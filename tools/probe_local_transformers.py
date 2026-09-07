"""Run one local, model-only Qwen tool-selection probe without executing a tool."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claw.transformers_client import TransformersToolClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-name", default="Qwen/Qwen3-1.7B")
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()
    client = TransformersToolClient(
        args.model_path,
        model_name=args.model_name,
        default_max_new_tokens=args.max_tokens,
        enable_thinking=False,
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read one file from the current workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]
    started = time.monotonic()
    response = client.complete(
        [
            {"role": "system", "content": "You are a coding agent. Use tools when required."},
            {"role": "user", "content": "Read README.md before answering. Call the read_file tool now."},
        ],
        tools=tools,
        tool_choice="required",
        temperature=0.0,
        max_tokens=args.max_tokens,
        thinking_mode="disabled",
    )
    calls = response.get("tool_calls") or []
    verified = (
        len(calls) == 1
        and calls[0].get("function", {}).get("name") == "read_file"
        and json.loads(calls[0]["function"]["arguments"]).get("path") == "README.md"
    )
    payload = {
        "schema_version": "local_transformers_tool_probe.v1",
        "model": client.model,
        "model_path": client.model_path,
        "device": client.device,
        "thinking": "disabled",
        "latency_seconds": round(time.monotonic() - started, 6),
        "usage": response.get("usage", {}),
        "finish_reason": response.get("finish_reason"),
        "tool_name": calls[0]["function"]["name"] if calls else None,
        "arguments": json.loads(calls[0]["function"]["arguments"]) if calls else None,
        "verified": verified,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
