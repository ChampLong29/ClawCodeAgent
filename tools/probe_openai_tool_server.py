"""Probe an OpenAI-compatible server for deterministic tool calling."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def _request(url: str, api_key: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--api-key", default="local-token")
    parser.add_argument("--model", default="Qwen/Qwen3-1.7B")
    parser.add_argument("--max-tokens", type=int, default=128)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    started = time.monotonic()
    try:
        models = _request(f"{base_url}/models", args.api_key)
        ready_seconds = time.monotonic() - started
        completion_started = time.monotonic()
        response = _request(
            f"{base_url}/chat/completions",
            args.api_key,
            {
                "model": args.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a coding agent. Use tools when required.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "Read README.md before answering. Call the read_file tool now."
                        ),
                    },
                ],
                "tools": [
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
                ],
                "tool_choice": "required",
                "temperature": 0.0,
                "max_tokens": args.max_tokens,
            },
        )
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}, sort_keys=True))
        return 1

    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    calls = message.get("tool_calls") or []
    arguments = None
    if calls:
        try:
            arguments = json.loads(calls[0].get("function", {}).get("arguments", ""))
        except json.JSONDecodeError:
            arguments = None
    verified = (
        len(calls) == 1
        and calls[0].get("function", {}).get("name") == "read_file"
        and arguments == {"path": "README.md"}
    )
    usage = response.get("usage", {})
    completion_seconds = time.monotonic() - completion_started
    output_tokens = usage.get("completion_tokens", 0)
    print(
        json.dumps(
            {
                "schema_version": "openai_tool_server_probe.v1",
                "server_models": [item.get("id") for item in models.get("data", [])],
                "requested_model": args.model,
                "ready_check_seconds": round(ready_seconds, 6),
                "completion_seconds": round(completion_seconds, 6),
                "output_tokens_per_second": (
                    round(output_tokens / completion_seconds, 3)
                    if output_tokens and completion_seconds
                    else None
                ),
                "usage": usage,
                "finish_reason": choice.get("finish_reason"),
                "tool_name": (
                    calls[0].get("function", {}).get("name") if calls else None
                ),
                "arguments": arguments,
                "verified": verified,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
