#!/usr/bin/env python3
"""Derive final-response delivery quality from an existing trajectory v2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claw.trajectory import Trajectory
from claw.verification import assess_final_response


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect empty, thinking-only, or raw tool-call final responses."
    )
    parser.add_argument("trajectory", type=Path)
    args = parser.parse_args()
    trajectory = Trajectory.from_dict(
        json.loads(args.trajectory.read_text(encoding="utf-8"))
    )
    termination = trajectory.header.termination
    assessment = assess_final_response(
        termination.detail if termination else "",
        completed=bool(termination and termination.reason == "completed"),
    )
    print(json.dumps(assessment.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
