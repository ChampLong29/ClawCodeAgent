"""Verify that a historical Python environment imports an Episode workspace."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from claw.data_pipeline.swe_bench_collection import (
    _infer_workspace_import_name,
    _probe_workspace_import,
    _workspace_pythonpath,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    python_executable = args.python.expanduser().absolute()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = _workspace_pythonpath(
        str(workspace), environment.get("PYTHONPATH", "")
    )
    environment["PATH"] = os.pathsep.join(
        [str(python_executable.parent), environment.get("PATH", "")]
    ).rstrip(os.pathsep)
    environment["VIRTUAL_ENV"] = str(python_executable.parent.parent)
    result = _probe_workspace_import(
        cwd=str(workspace),
        python_executable=python_executable,
        import_name=_infer_workspace_import_name(args.repo, str(workspace)),
        environment=environment,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
