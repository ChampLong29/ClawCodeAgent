"""Build an auditable catalog and curated pilot from SWE-bench Lite dev."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "benchmarks" / "swe_bench_lite"
SCHEMA_VERSION = "swe_bench_lite_selection.v1"

REPO_PROFILES = {
    "marshmallow-code/marshmallow": {
        "environment_class": "light",
        "environment_score": 5,
        "license": "MIT",
    },
    "pylint-dev/astroid": {
        "environment_class": "light",
        "environment_score": 4,
        "license": "LGPL-2.1-or-later",
    },
    "pydicom/pydicom": {
        "environment_class": "moderate",
        "environment_score": 4,
        "license": "MIT",
    },
    "sqlfluff/sqlfluff": {
        "environment_class": "moderate",
        "environment_score": 3,
        "license": "MIT",
    },
    "pvlib/pvlib-python": {
        "environment_class": "scientific",
        "environment_score": 2,
        "license": "BSD-3-Clause",
    },
    "pyvista/pyvista": {
        "environment_class": "heavy-vtk",
        "environment_score": 0,
        "license": "MIT",
    },
}

PILOT = {
    "marshmallow-code__marshmallow-1343": {
        "role": "real-issue calibration",
        "rationale": (
            "Small MIT repository and one focused regression test; provides a "
            "low-cost check that the external harness is wired correctly."
        ),
    },
    "pylint-dev__astroid-1196": {
        "role": "semantic reasoning",
        "rationale": (
            "Pure-Python AST inference bug with two FAIL_TO_PASS tests and a "
            "non-trivial reference change; exercises inference and exception semantics."
        ),
    },
    "sqlfluff__sqlfluff-1763": {
        "role": "filesystem safety",
        "rationale": (
            "Encoding failure can corrupt a file; three FAIL_TO_PASS cases exercise "
            "safe replacement, cleanup, and rollback behavior."
        ),
    },
    "pydicom__pydicom-1139": {
        "role": "python data-model protocol",
        "rationale": (
            "Pure-Python iteration and containment behavior with three focused "
            "FAIL_TO_PASS tests; promoted after the initial three-issue pilot."
        ),
    },
    "pvlib__pvlib-python-1707": {
        "role": "scientific numerical boundary",
        "rationale": (
            "Focused one-file incidence-angle regression with one target test and "
            "30 regression tests; adds a fifth repository family while keeping the "
            "scientific dependency surface bounded."
        ),
    },
    "pydicom__pydicom-1413": {
        "role": "within-family contract generalization",
        "rationale": (
            "A second, non-overlapping pydicom issue checks whether the runtime's "
            "post-edit contract notice generalizes from scientific return types to "
            "bytes-versus-MultiValue semantics without introducing a new repository."
        ),
    },
    "pylint-dev__astroid-1333": {
        "role": "path-resolution generalization",
        "rationale": (
            "A second Astroid issue exercises namespace-package path ordering "
            "with 46 PASS_TO_PASS tests and provides a fresh repository path for "
            "validating implementation-scoped post-edit guidance."
        ),
    },
}

FALLBACK = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_list(value: str) -> List[str]:
    result = json.loads(value)
    if not isinstance(result, list) or not all(
        isinstance(item, str) for item in result
    ):
        raise ValueError("SWE-bench test fields must contain JSON string lists")
    return result


def _diff_files(patch: str) -> List[str]:
    return re.findall(r"(?m)^diff --git a/(\S+) b/\S+", patch)


def _changed_lines(patch: str) -> int:
    return sum(
        1
        for line in patch.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )


def _complexity_score(changed_lines: int) -> int:
    if 8 <= changed_lines <= 80:
        return 5
    if 4 <= changed_lines <= 7 or 81 <= changed_lines <= 150:
        return 3
    return 1


def _problem_score(length: int) -> int:
    if 300 <= length <= 5000:
        return 5
    if 150 <= length <= 8000:
        return 4
    return 2


def _test_score(fail_count: int) -> int:
    if 2 <= fail_count <= 4:
        return 5
    if fail_count == 1:
        return 4
    return 2


def build_catalog(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    catalog = []
    for row in rows:
        profile = REPO_PROFILES.get(
            row["repo"],
            {
                "environment_class": "unclassified",
                "environment_score": 1,
                "license": "needs-review",
            },
        )
        fail_to_pass = _json_list(row["FAIL_TO_PASS"])
        pass_to_pass = _json_list(row["PASS_TO_PASS"])
        patch_files = _diff_files(row["patch"])
        test_files = _diff_files(row["test_patch"])
        changed_lines = _changed_lines(row["patch"])
        scores = {
            "environment": profile["environment_score"],
            "problem_statement": _problem_score(len(row["problem_statement"])),
            "change_complexity": _complexity_score(changed_lines),
            "regression_tests": _test_score(len(fail_to_pass)),
            "focused_patch": 5 if len(patch_files) == 1 else 3,
        }
        title = row["problem_statement"].splitlines()[0].strip()
        catalog.append(
            {
                "instance_id": row["instance_id"],
                "repo": row["repo"],
                "version": row["version"],
                "base_commit": row["base_commit"],
                "environment_setup_commit": row["environment_setup_commit"],
                "created_at": row["created_at"],
                "title": title,
                "problem_statement_chars": len(row["problem_statement"]),
                "fail_to_pass_count": len(fail_to_pass),
                "pass_to_pass_count": len(pass_to_pass),
                "patch_file_count": len(patch_files),
                "patch_changed_lines": changed_lines,
                "test_patch_file_count": len(test_files),
                "environment_class": profile["environment_class"],
                "license": profile["license"],
                "scores": scores,
                "total_score": sum(scores.values()),
                "selected": row["instance_id"] in PILOT,
                "fallback": bool(FALLBACK and row["instance_id"] == FALLBACK),
            }
        )
    return sorted(catalog, key=lambda item: item["instance_id"])


def generate(root: Path = DEFAULT_ROOT) -> Dict[str, Path]:
    root = root.resolve()
    raw_rows = root / "raw" / "dev.rows.json"
    raw_metadata = root / "raw" / "dataset-metadata.json"
    raw_parquet = root / "raw" / "dev-00000-of-00001.parquet"
    rows_payload = json.loads(raw_rows.read_text(encoding="utf-8"))
    metadata = json.loads(raw_metadata.read_text(encoding="utf-8"))
    rows = [entry["row"] for entry in rows_payload["rows"]]
    if rows_payload.get("num_rows_total") != 23 or len(rows) != 23:
        raise ValueError("expected exactly 23 SWE-bench Lite dev rows")
    catalog = build_catalog(rows)
    by_id = {item["instance_id"]: item for item in catalog}
    required = set(PILOT) | ({FALLBACK} if FALLBACK else set())
    missing = sorted(required - set(by_id))
    if missing:
        raise ValueError(f"selected instances are missing: {missing}")

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "dataset": "SWE-bench/SWE-bench_Lite",
        "split": "dev",
        "dataset_revision": metadata["sha"],
        "dataset_last_modified": metadata["lastModified"],
        "row_count": len(rows),
        "rows_sha256": _sha256(raw_rows),
        "metadata_sha256": _sha256(raw_metadata),
        "parquet_sha256": _sha256(raw_parquet),
        "source_urls": {
            "dataset": "https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite",
            "rows_api": (
                "https://datasets-server.huggingface.co/rows?dataset="
                "SWE-bench%2FSWE-bench_Lite&config=default&split=dev&offset=0&length=100"
            ),
        },
    }
    selection = {
        "schema_version": SCHEMA_VERSION,
        "dataset_revision": metadata["sha"],
        "split": "dev",
        "policy": {
            "max_instances": 7,
            "one_instance_per_repository": False,
            "minimum_distinct_repositories": 5,
            "maximum_instances_per_repository": 2,
            "selection_dimensions": [
                "environment weight",
                "problem statement completeness",
                "reference change complexity",
                "FAIL_TO_PASS coverage",
                "focused patch scope",
                "behavioral diversity",
            ],
            "excluded_from_agent_context": [
                "patch",
                "test_patch",
                "hints_text",
                "FAIL_TO_PASS",
                "PASS_TO_PASS",
            ],
        },
        "selected": [
            {
                **by_id[instance_id],
                **PILOT[instance_id],
                "local_repo": f"repos/{instance_id}",
            }
            for instance_id in PILOT
        ],
        "deferred_environment_classes": ["heavy-vtk"],
    }
    if FALLBACK:
        selection["fallback"] = {
            **by_id[FALLBACK],
            "rationale": "Reserved fallback outside the active pilot.",
        }
    raw_by_id = {row["instance_id"]: row for row in rows}
    agent_inputs = {
        "schema_version": SCHEMA_VERSION,
        "dataset_revision": metadata["sha"],
        "split": "dev",
        "instances": [
            {
                "instance_id": instance_id,
                "repo": raw_by_id[instance_id]["repo"],
                "base_commit": raw_by_id[instance_id]["base_commit"],
                "version": raw_by_id[instance_id]["version"],
                "problem_statement": raw_by_id[instance_id][
                    "problem_statement"
                ],
            }
            for instance_id in PILOT
        ],
    }

    outputs = {
        "snapshot": root / "snapshot.json",
        "catalog": root / "dev-catalog.json",
        "selection": root / "pilot-selection.json",
        "agent_inputs": root / "pilot-agent-inputs.json",
    }
    payloads = {
        "snapshot": snapshot,
        "catalog": {
            "schema_version": SCHEMA_VERSION,
            "dataset_revision": metadata["sha"],
            "split": "dev",
            "instances": catalog,
        },
        "selection": selection,
        "agent_inputs": agent_inputs,
    }
    for name, destination in outputs.items():
        destination.write_text(
            json.dumps(payloads[name], ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    outputs = generate(args.root)
    print(json.dumps({key: str(value) for key, value in outputs.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
