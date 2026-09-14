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
rm .claw-container-probe
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
    args = parser.parse_args()

    runner = OCIContainerRunner(
        OCIContainerConfig(
            image=args.image,
            engine=args.engine,
            cpus=1.0,
            memory="128m",
            pids_limit=32,
            tmpfs_size="16m",
        )
    )
    disposable_contract = runner.verify_disposable_workspace_contract()
    if args.workspace:
        workspace = Path(args.workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        command = f"cd {shlex.quote(workspace.as_posix())}\n{PROBE_COMMAND}"
        result = runner.run(command, cwd=str(workspace), timeout=30)
    else:
        with tempfile.TemporaryDirectory(prefix="claw-container-probe-") as temp:
            workspace = Path(temp).resolve()
            command = f"cd {shlex.quote(workspace.as_posix())}\n{PROBE_COMMAND}"
            result = runner.run(command, cwd=temp, timeout=30)
    payload = {
        "schema_version": "claw_container_probe.v1",
        "execution_backend": runner.describe(),
        "returncode": result.returncode,
        "stdout": result.stdout.splitlines(),
        "stderr": result.stderr.splitlines(),
        "command_metadata": runner.result_metadata(result),
        "host_workspace_path_translated": (
            runner.result_metadata(result)["effective_command"].startswith(
                f"cd {runner.config.workspace_target}"
            )
        ),
        "disposable_workspace_contract": disposable_contract,
        "verified": (
            result.returncode == 0
            and disposable_contract.get("status") == "passed"
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
