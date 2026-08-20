#!/usr/bin/env python3
"""Derive behavior diagnostics from an existing trajectory v2 artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claw.experiment.artifacts import ArtifactStore
from claw.trajectory import Trajectory, analyze_rollout_behavior


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze path-localization and edit timing in a trajectory."
    )
    parser.add_argument("trajectory", type=Path)
    parser.add_argument(
        "--target-path",
        action="append",
        default=[],
        help="Expected implementation path or glob; repeat as needed.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    trajectory = Trajectory.from_dict(
        json.loads(args.trajectory.read_text(encoding="utf-8"))
    )
    artifact_root = args.trajectory.resolve().parent / "artifacts"
    artifact_store = ArtifactStore(artifact_root) if artifact_root.is_dir() else None

    def resolve_payload(event):
        payload = dict(event.payload)
        if artifact_store is None or "artifact_ref" not in payload:
            return payload
        resolved = artifact_store.get_json(payload["artifact_ref"])
        if isinstance(resolved, dict):
            payload.update(resolved)
        return payload

    diagnostics = analyze_rollout_behavior(
        trajectory,
        target_path_patterns=args.target_path,
        payload_resolver=resolve_payload,
    )
    print(json.dumps(diagnostics.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
