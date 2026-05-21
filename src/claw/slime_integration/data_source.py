"""Task data source for SLIME integration.

Converts Claw's coding task format to SLIME's Sample format.
Supports loading from JSONL files with the schema:

    {"prompt": "...", "label": "...", "metadata": {...}}

Where metadata can include:
- test_commands: list[str] — commands to verify the solution
- ground_truth_files: dict[str, str] — expected file contents
- template_dir: str — initial project template path
- lifecycle_phase: str — which phase constraints to apply
- domain: str — task domain (web_backend, cli, sdk, etc.)
- difficulty: str — easy/medium/hard (for curriculum)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid SLIME dependency in normal mode
_Sample = None


def _get_sample_class():
    global _Sample
    if _Sample is None:
        from slime.utils.types import Sample
        _Sample = Sample
    return _Sample


def load_tasks_as_samples(
    jsonl_path: str,
    n_samples_per_prompt: int = 1,
    max_tasks: Optional[int] = None,
) -> List[List[Any]]:
    """Load a JSONL task file and convert to SLIME Sample groups.

    Each task becomes a sample group (list of Samples). If
    n_samples_per_prompt > 1, duplicate samples are created for
    the same prompt (GRPO needs multiple rollouts per prompt).

    Args:
        jsonl_path: Path to the task JSONL file
        n_samples_per_prompt: Number of rollouts per prompt (for GRPO)
        max_tasks: Maximum number of tasks to load (for debugging)

    Returns:
        list[list[Sample]] — grouped samples for SLIME consumption
    """
    Sample = _get_sample_class()
    sample_groups = []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for group_idx, line in enumerate(f):
            if max_tasks and group_idx >= max_tasks:
                break

            line = line.strip()
            if not line:
                continue

            try:
                task = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Skipping invalid JSON at line {group_idx + 1}")
                continue

            prompt = task.get("prompt", "")
            label = task.get("label")
            metadata = task.get("metadata", {})

            # Create sample group
            group = []
            for sample_idx in range(n_samples_per_prompt):
                sample = Sample(
                    group_index=group_idx,
                    index=sample_idx,
                    prompt=prompt,
                    label=label,
                    metadata=dict(metadata),  # Copy to avoid shared mutation
                    status=Sample.Status.PENDING,
                )
                group.append(sample)

            sample_groups.append(group)

    logger.info(
        f"Loaded {len(sample_groups)} tasks from {jsonl_path} "
        f"({n_samples_per_prompt} samples/prompt)"
    )
    return sample_groups


def create_sample_tasks_jsonl(
    output_path: str,
    tasks: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Create a sample tasks.jsonl for testing.

    If no tasks provided, generates minimal examples.
    Returns the path to the created file.
    """
    if tasks is None:
        tasks = [
            {
                "prompt": "Create a Python function called `add` in `math_utils.py` that takes two integers and returns their sum. Include type hints.",
                "label": "def add(a: int, b: int) -> int:\n    return a + b\n",
                "metadata": {
                    "test_commands": ["python -c \"from math_utils import add; assert add(2,3)==5; assert add(-1,1)==0; print('PASS')\""],
                    "ground_truth_files": {
                        "math_utils.py": "def add(a: int, b: int) -> int:\n    return a + b\n"
                    },
                    "lifecycle_phase": "IMPLEMENTATION",
                    "domain": "cli",
                    "difficulty": "easy",
                },
            },
            {
                "prompt": "Create a file `fizzbuzz.py` with a function `fizzbuzz(n: int) -> str` that returns 'Fizz' for multiples of 3, 'Buzz' for multiples of 5, 'FizzBuzz' for multiples of both, and the number as string otherwise.",
                "label": None,
                "metadata": {
                    "test_commands": [
                        "python -c \"from fizzbuzz import fizzbuzz; assert fizzbuzz(3)=='Fizz'; assert fizzbuzz(5)=='Buzz'; assert fizzbuzz(15)=='FizzBuzz'; assert fizzbuzz(7)=='7'; print('PASS')\""
                    ],
                    "lifecycle_phase": "IMPLEMENTATION",
                    "domain": "cli",
                    "difficulty": "easy",
                },
            },
            {
                "prompt": "Write the requirements specification for a student club management system. The system should support club registration, member management, and activity scheduling. Output a structured markdown document.",
                "label": None,
                "metadata": {
                    "lifecycle_phase": "REQUIREMENTS",
                    "domain": "web_backend",
                    "difficulty": "medium",
                },
            },
        ]

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")

    logger.info(f"Created {len(tasks)} sample tasks at {output_path}")
    return output_path
