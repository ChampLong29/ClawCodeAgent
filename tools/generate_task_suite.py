"""Generate the deterministic 32-task executable M1 suite."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claw.episode.checkpoint import workspace_hash
from claw.experiment.schemas import TaskSpec, canonical_hash


COMMAND = "python -m unittest -q test_challenge.py"
GENERATOR_REF = "tools/generate_task_suite.py"


def _split(index: int) -> str:
    if index <= 5:
        return "train"
    if index == 6:
        return "dev"
    return "test"


def _difficulty(index: int) -> str:
    if index <= 3:
        return "easy"
    if index <= 6:
        return "medium"
    return "hard"


def _case(
    *,
    domain: str,
    task_type: str,
    index: int,
) -> Dict[str, str]:
    if domain == "python-cli" and task_type == "add_feature":
        prefix = f"run{index}"
        return {
            "prompt": (
                f"Implement build_command(name) so a non-empty string becomes "
                f"'{prefix}:<name>'; reject invalid names with ValueError."
            ),
            "template": (
                '"""Command formatting challenge."""\n\n'
                "def build_command(name):\n"
                '    raise NotImplementedError("implement build_command")\n'
            ),
            "oracle": (
                '"""Command formatting challenge."""\n\n'
                "def build_command(name):\n"
                "    if not isinstance(name, str) or not name:\n"
                '        raise ValueError("name must be a non-empty string")\n'
                f'    return f"{prefix}:{{name}}"\n'
            ),
            "test": (
                "import unittest\n\n"
                "from challenge import build_command\n\n\n"
                "class ChallengeTest(unittest.TestCase):\n"
                "    def test_formats_command(self):\n"
                f'        self.assertEqual(build_command("alpha"), "{prefix}:alpha")\n\n'
                "    def test_rejects_empty_name(self):\n"
                "        with self.assertRaises(ValueError):\n"
                '            build_command("")\n\n\n'
                'if __name__ == "__main__":\n'
                "    unittest.main()\n"
            ),
        }
    if domain == "python-cli" and task_type == "fix_bug":
        maximum = 8000 + index
        return {
            "prompt": (
                f"Fix parse_port(value) to return an integer from 1 through "
                f"{maximum}, rejecting non-numeric and out-of-range values."
            ),
            "template": (
                '"""Port parsing challenge."""\n\n'
                "def parse_port(value):\n"
                "    return value\n"
            ),
            "oracle": (
                '"""Port parsing challenge."""\n\n'
                "def parse_port(value):\n"
                "    try:\n"
                "        port = int(value)\n"
                "    except (TypeError, ValueError) as exc:\n"
                '        raise ValueError("invalid port") from exc\n'
                f"    if not 1 <= port <= {maximum}:\n"
                '        raise ValueError("port out of range")\n'
                "    return port\n"
            ),
            "test": (
                "import unittest\n\n"
                "from challenge import parse_port\n\n\n"
                "class ChallengeTest(unittest.TestCase):\n"
                "    def test_parses_numeric_text(self):\n"
                f'        self.assertEqual(parse_port("{maximum}"), {maximum})\n\n'
                "    def test_rejects_out_of_range(self):\n"
                "        with self.assertRaises(ValueError):\n"
                f"            parse_port({maximum + 1})\n\n"
                "    def test_rejects_non_numeric(self):\n"
                "        with self.assertRaises(ValueError):\n"
                '            parse_port("http")\n\n\n'
                'if __name__ == "__main__":\n'
                "    unittest.main()\n"
            ),
        }
    if domain == "python-library" and task_type == "add_feature":
        factor = index + 1
        return {
            "prompt": (
                f"Implement weighted_total(values) as the numeric sum multiplied "
                f"by {factor}; reject non-list inputs with TypeError."
            ),
            "template": (
                '"""Weighted total challenge."""\n\n'
                "def weighted_total(values):\n"
                '    raise NotImplementedError("implement weighted_total")\n'
            ),
            "oracle": (
                '"""Weighted total challenge."""\n\n'
                "def weighted_total(values):\n"
                "    if not isinstance(values, list):\n"
                '        raise TypeError("values must be a list")\n'
                f"    return sum(values) * {factor}\n"
            ),
            "test": (
                "import unittest\n\n"
                "from challenge import weighted_total\n\n\n"
                "class ChallengeTest(unittest.TestCase):\n"
                "    def test_applies_weight(self):\n"
                f"        self.assertEqual(weighted_total([1, 2, 3]), {6 * factor})\n\n"
                "    def test_empty_list(self):\n"
                "        self.assertEqual(weighted_total([]), 0)\n\n"
                "    def test_rejects_non_list(self):\n"
                "        with self.assertRaises(TypeError):\n"
                "            weighted_total((1, 2))\n\n\n"
                'if __name__ == "__main__":\n'
                "    unittest.main()\n"
            ),
        }
    marker = f"item-{index}"
    return {
        "prompt": (
            "Fix stable_unique(values) so duplicates are removed while the "
            f"original order is preserved, including the marker '{marker}'."
        ),
        "template": (
            '"""Stable uniqueness challenge."""\n\n'
            "def stable_unique(values):\n"
            "    return sorted(set(values))\n"
        ),
        "oracle": (
            '"""Stable uniqueness challenge."""\n\n'
            "def stable_unique(values):\n"
            "    result = []\n"
            "    for value in values:\n"
            "        if value not in result:\n"
            "            result.append(value)\n"
            "    return result\n"
        ),
        "test": (
            "import unittest\n\n"
            "from challenge import stable_unique\n\n\n"
            "class ChallengeTest(unittest.TestCase):\n"
            "    def test_preserves_first_seen_order(self):\n"
            f'        values = ["z", "{marker}", "z", "a", "{marker}"]\n'
            f'        self.assertEqual(stable_unique(values), ["z", "{marker}", "a"])\n\n'
            "    def test_empty_list(self):\n"
            "        self.assertEqual(stable_unique([]), [])\n\n\n"
            'if __name__ == "__main__":\n'
            "    unittest.main()\n"
        ),
    }


def generate_suite(output_root: Path) -> Path:
    output_root = output_root.resolve()
    templates_root = output_root / "templates"
    oracles_root = output_root / "oracles"
    templates_root.mkdir(parents=True, exist_ok=True)
    oracles_root.mkdir(parents=True, exist_ok=True)
    tasks: List[TaskSpec] = []
    for domain in ("python-cli", "python-library"):
        for task_type in ("add_feature", "fix_bug"):
            for index in range(1, 9):
                task_id = f"{domain}-{task_type}-{index:02d}"
                case = _case(
                    domain=domain,
                    task_type=task_type,
                    index=index,
                )
                template = templates_root / task_id
                oracle = oracles_root / task_id
                if template.exists():
                    shutil.rmtree(template)
                if oracle.exists():
                    shutil.rmtree(oracle)
                template.mkdir(parents=True)
                oracle.mkdir(parents=True)
                (template / "challenge.py").write_text(
                    case["template"], encoding="utf-8", newline="\n"
                )
                (template / "test_challenge.py").write_text(
                    case["test"], encoding="utf-8", newline="\n"
                )
                (oracle / "challenge.py").write_text(
                    case["oracle"], encoding="utf-8", newline="\n"
                )
                relative_template = template.relative_to(
                    output_root.parent
                ).as_posix()
                relative_oracle = oracle.relative_to(
                    output_root.parent
                ).as_posix()
                task = TaskSpec(
                    task_id=task_id,
                    task_version="1.0.0",
                    family_id=f"family-{task_id}",
                    domain=domain,
                    task_type=task_type,
                    difficulty=_difficulty(index),
                    split=_split(index),
                    prompt=case["prompt"],
                    template_ref=relative_template,
                    template_hash=workspace_hash(template, normalize_exec=True),
                    initial_checks=[COMMAND],
                    test_commands=[COMMAND],
                    oracle_ref=relative_oracle,
                    timeout_seconds=30.0,
                    resource_limits={
                        "cpu_seconds": 10,
                        "memory_mb": 256,
                        "processes": 4,
                    },
                    source="claw-code-agent-generated",
                    license="MIT",
                    tags=[domain, task_type, _difficulty(index)],
                )
                task.content_hash = task.compute_content_hash()
                tasks.append(task)

    description = (
        "Deterministic executable coding suite covering Python CLI and library "
        "tasks, two task types, three family-safe splits, and reference fixes."
    )
    generated_by = GENERATOR_REF
    content_hash = canonical_hash(
        {
            "suite_id": "claw-m1-core",
            "version": "1.0.0",
            "description": description,
            "generated_by": generated_by,
            "tasks": [task.to_dict() for task in tasks],
        }
    )
    manifest = {
        "schema_version": "task_suite.v1",
        "suite_id": "claw-m1-core",
        "version": "1.0.0",
        "description": description,
        "generated_by": generated_by,
        "content_hash": content_hash,
        "tasks": [task.to_dict() for task in tasks],
    }
    destination = output_root / "manifest.json"
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


def main() -> None:
    destination = generate_suite(PROJECT_ROOT / "task_suites")
    print(destination)


if __name__ == "__main__":
    main()
