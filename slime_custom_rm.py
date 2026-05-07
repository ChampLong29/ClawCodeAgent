#!/usr/bin/env python3
"""SLIME custom reward function — sandbox-based code verification.

Usage with slime::

    slime --mode rl --custom-rm-path ./slime_custom_rm.py ...

This script is **standalone** — it only depends on the Python stdlib
so it can be passed directly to slime's ``--custom-rm-path``.

The reward function reconstructs written files from the agent's
response text, runs test commands in a temp directory, and computes a
combined reward from test pass rate + diff accuracy.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Any, Dict, List, Optional


def extract_task_from_prompt(prompt: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract task definition from the prompt messages.

    Looks for a system message containing ``task_definition`` JSON or
    test_commands embedded in the prompt.
    """
    for msg in prompt:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue

        # Try to find embedded JSON task definition
        m = re.search(r'```json\s*(\{[\s\S]*?"test_commands"[\s\S]*?\})\s*```', content)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # Try test_commands as bullet list
        m = re.search(
            r'test_commands:\s*\n((?:\s*-.*\n?)+)', content, re.IGNORECASE
        )
        if m:
            commands = re.findall(r'-\s*`?(.+?)`?\s*$', m.group(1), re.MULTILINE)
            if commands:
                return {"test_commands": commands}

    return None


def extract_written_files(response: List[Dict[str, Any]]) -> Dict[str, str]:
    """Extract file writes from agent response messages.

    Looks for ``write_file`` tool calls in assistant messages, or
    code blocks with file paths.
    """
    files: Dict[str, str] = {}

    for msg in response:
        # Check for tool_calls in assistant messages
        if msg.get("role") == "assistant":
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                if tc.get("name") == "write_file":
                    args = tc.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            continue
                    file_path = args.get("file_path", "")
                    content = args.get("content", "")
                    if file_path and content:
                        files[file_path] = content

        # Check for tool result messages (actual file writes confirmed)
        if msg.get("role") == "tool" and msg.get("tool_name") == "write_file":
            content = msg.get("content", "")
            # Content is usually a confirmation, not the file content
            # The file content is in the assistant's tool_call arguments

    return files


def reconstruct_sandbox(files: Dict[str, str]) -> str:
    """Create a temp directory with the given files. Returns the path."""
    sandbox = tempfile.mkdtemp(prefix="slime_rm_")
    for rel_path, content in files.items():
        full_path = os.path.join(sandbox, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
    return sandbox


def run_tests(sandbox_dir: str, test_commands: List[str]) -> Dict[str, Any]:
    """Run test commands in the sandbox. Returns pass rate and output."""
    passed = 0
    total = len(test_commands)
    outputs: List[str] = []

    for cmd in test_commands:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = proc.stdout + "\n" + proc.stderr
            outputs.append(f"$ {cmd}\n{output}")
            if proc.returncode == 0:
                passed += 1
        except subprocess.TimeoutExpired:
            outputs.append(f"$ {cmd}\nTIMEOUT (120s)")
        except Exception as e:
            outputs.append(f"$ {cmd}\nERROR: {e}")

    pass_rate = passed / total if total > 0 else 0.0
    return {
        "pass_rate": pass_rate,
        "passed": passed,
        "total": total,
        "output": "\n".join(outputs),
    }


def compute_diff(
    sandbox_dir: str, ground_truth: Dict[str, str]
) -> Dict[str, Any]:
    """Compare written files against ground truth."""
    total = len(ground_truth)
    if total == 0:
        return {"match_rate": 1.0, "matches": 0, "total": 0}

    matches = 0
    for rel_path, expected in ground_truth.items():
        file_path = os.path.join(sandbox_dir, rel_path)
        if not os.path.isfile(file_path):
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            actual = f.read()
        if actual.strip() == expected.strip():
            matches += 1

    return {
        "match_rate": matches / total,
        "matches": matches,
        "total": total,
    }


def _compute_process_reward(
    prompt: Any, response: Any
) -> float:
    """Compute process reward from prompt/response text.

    Checks for phase boundary markers indicating the agent followed
    the correct engineering flow.
    """
    prompt_text = _normalize_text(prompt)
    response_text = _normalize_text(response)

    score = 0.0
    checks = 0

    # Check for architecture/design before implementation
    if "ARCHITECTURE" in prompt_text or "design" in prompt_text.lower():
        score += 0.5; checks += 1
    if "## Architecture" in response_text or "## System Design" in response_text:
        score += 0.5; checks += 1

    # Check for test execution evidence
    if "pytest" in response_text.lower() or "test" in response_text.lower():
        score += 1.0; checks += 1

    return score / max(checks, 1) if checks > 0 else 0.5


def _compute_format_reward(prompt: Any) -> float:
    """Compute format reward from output structure."""
    text = _normalize_text(prompt)
    if not text:
        return 0.5

    dims = 0
    score = 0.0

    if re.search(r'^#{1,3}\s+\S', text, re.MULTILINE):
        score += 1.0; dims += 1
    if re.search(r'(^- |^\|.+\|)', text, re.MULTILINE):
        score += 1.0; dims += 1
    if 200 <= len(text) <= 10000:
        score += 1.0; dims += 1
    if "```" in text:
        score += 1.0; dims += 1

    return score / dims if dims > 0 else 0.5


def _normalize_text(obj: Any) -> str:
    """Convert prompt/response to a single string for analysis."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return "\n".join(
            m.get("content", "") for m in obj
            if isinstance(m, dict) and m.get("content")
        )
    return str(obj)


# ====================================================================
# Main entry point — called by slime
# ====================================================================

def compute_reward(
    prompt: Any,
    response: Any,
    **kwargs: Any,
) -> float:
    """SLIME custom reward function.

    Args:
        prompt: List of prompt messages (system + user).
        response: List of response messages (assistant + tool).
        **kwargs: Additional slime metadata (ignored).

    Returns:
        float reward in [0.0, 1.0].
    """
    # Normalize inputs
    if isinstance(prompt, str):
        try:
            prompt = json.loads(prompt)
        except json.JSONDecodeError:
            return 0.0
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            return 0.0

    if not isinstance(prompt, list) or not isinstance(response, list):
        return 0.0

    # Extract task definition
    task = extract_task_from_prompt(prompt)
    test_commands = task.get("test_commands", []) if task else []
    ground_truth = task.get("ground_truth_files", {}) if task else {}

    if not test_commands and not ground_truth:
        return 0.5  # No verifiable criteria — neutral reward

    # Extract written files
    files = extract_written_files(response)

    if not files:
        return 0.0  # Agent didn't write any files

    # Reconstruct sandbox
    sandbox_dir = reconstruct_sandbox(files)

    try:
        # Run tests
        test_result = run_tests(sandbox_dir, test_commands)
        test_reward = test_result["pass_rate"]

        # Compute diff
        diff_result = compute_diff(sandbox_dir, ground_truth)
        diff_reward = diff_result["match_rate"]

        # Combined reward (with process + format signals)
        process_score = _compute_process_reward(prompt, response)
        format_score = _compute_format_reward(prompt)

        if test_commands and ground_truth:
            reward = test_reward * 0.30 + diff_reward * 0.20 + process_score * 0.25 + format_score * 0.25
        elif test_commands:
            reward = test_reward * 0.40 + process_score * 0.30 + format_score * 0.30
        elif ground_truth:
            reward = diff_reward * 0.40 + process_score * 0.30 + format_score * 0.30
        else:
            reward = process_score * 0.50 + format_score * 0.50

        return max(0.0, min(1.0, reward))

    finally:
        # Cleanup temp sandbox
        import shutil
        shutil.rmtree(sandbox_dir, ignore_errors=True)


# ====================================================================
# Standalone test
# ====================================================================

if __name__ == "__main__":
    # Quick smoke test
    test_prompt = [
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Write a hello.py that prints 'hello'"},
    ]
    test_response = [
        {
            "role": "assistant",
            "content": "I'll create hello.py",
            "tool_calls": [{
                "name": "write_file",
                "arguments": {
                    "file_path": "hello.py",
                    "content": "print('hello')",
                },
            }],
        },
        {"role": "tool", "content": "File written: hello.py"},
    ]

    reward = compute_reward(test_prompt, test_response)
    print(f"Smoke test reward: {reward}")
