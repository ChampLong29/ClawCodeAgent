"""Probe the real OCI boundary without running a model or task suite."""

from __future__ import annotations

import argparse
import json
import shlex
import tempfile
from pathlib import Path

from claw.container_runtime import OCIContainerConfig, OCIContainerRunner


PROBE_COMMAND = """set -eu
printf 'workspace=ok\\n' > .claw-container-probe
cat .claw-container-probe
printf 'tmp=ok\\n' > /tmp/claw-container-probe
awk '$2 == "/" && $4 ~ /(^|,)ro(,|$)/ { found=1 } END { exit !found }' /proc/mounts
test ! -e /sys/class/net/eth0
grep -q '^CapEff:[[:space:]]*0000000000000000$' /proc/self/status
printf 'rootfs=blocked\\nnetwork=none\\ncapabilities=none\\n'
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--engine", choices=("auto", "docker", "podman"), default="auto"
    )
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--workspace-copy-mode",
        choices=("container", "host"),
        default="container",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat the exact workspace probe without invoking a model.",
    )
    parser.add_argument("--task-python", default=None)
    parser.add_argument("--import-name", default=None)
    args = parser.parse_args()
    if args.repeat <= 0:
        parser.error("--repeat must be positive")
    if bool(args.task_python) != bool(args.import_name):
        parser.error("--task-python and --import-name must be supplied together")

    runner = OCIContainerRunner(
        OCIContainerConfig(
            image=args.image,
            engine=args.engine,
            cpus=1.0,
            memory="128m",
            pids_limit=32,
            tmpfs_size="16m",
            workspace_copy_mode=args.workspace_copy_mode,
        )
    )
    disposable_contract = runner.verify_disposable_workspace_contract()
    temporary = None
    if args.workspace:
        workspace = Path(args.workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="claw-container-probe-")
        workspace = Path(temporary.name).resolve()
    marker = workspace / ".claw-container-probe"
    if marker.exists():
        raise SystemExit(f"refusing to replace existing probe marker: {marker}")
    task_import_command = ""
    if args.task_python:
        import_script = (
            "import importlib,pathlib,sys;"
            "root=pathlib.Path.cwd().resolve();"
            "sys.path[:0]=[str(root/'src'),str(root)];"
            "origin=pathlib.Path(importlib.import_module(sys.argv[1]).__file__).resolve();"
            "origin.relative_to(root);"
            "print('task-import=ok')"
        )
        task_import_command = (
            f"{shlex.quote(args.task_python)} -c {shlex.quote(import_script)} "
            f"{shlex.quote(args.import_name)}\n"
        )
    command = (
        f"cd {shlex.quote(workspace.as_posix())}\n"
        f"{task_import_command}{PROBE_COMMAND}"
    )
    results = [
        runner.run(command, cwd=str(workspace), timeout=30)
        for _ in range(args.repeat)
    ]
    source_marker_absent = not marker.exists()
    if marker.exists():
        marker.unlink()
    if temporary is not None:
        temporary.cleanup()
    result = results[-1]
    attempts = [
        {
            "attempt": index,
            "returncode": item.returncode,
            "stdout": item.stdout.splitlines(),
            "stderr": item.stderr.splitlines(),
            "command_metadata": runner.result_metadata(item),
        }
        for index, item in enumerate(results, start=1)
    ]
    payload = {
        "schema_version": "claw_container_probe.v1",
        "execution_backend": runner.describe(),
        "returncode": result.returncode,
        "stdout": result.stdout.splitlines(),
        "stderr": result.stderr.splitlines(),
        "command_metadata": runner.result_metadata(result),
        "attempts": attempts,
        "repeat": args.repeat,
        "host_workspace_path_translated": (
            runner.result_metadata(result)["effective_command"].startswith(
                f"cd {runner.config.workspace_target}"
            )
        ),
        "source_workspace_marker_absent": source_marker_absent,
        "task_import_verified": (
            not args.task_python
            or all("task-import=ok" in item.stdout.splitlines() for item in results)
        ),
        "disposable_workspace_contract": disposable_contract,
        "verified": (
            all(item.returncode == 0 for item in results)
            and disposable_contract.get("status") == "passed"
            and source_marker_absent
            and (
                not args.task_python
                or all("task-import=ok" in item.stdout.splitlines() for item in results)
            )
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
