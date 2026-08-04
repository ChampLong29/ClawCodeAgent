# SWE-bench Lite Dev Pilot

This directory contains a pinned snapshot and a small curated pilot. It does
not contain the 300-instance test split.

## Contents

- `raw/dev.rows.json`: 23 dev rows downloaded from the official Hugging Face
  Dataset Server API. This file contains gold and test patches and must never
  be mounted into an agent workspace.
- `raw/dev-00000-of-00001.parquet`: official dev Parquet pinned to the recorded
  dataset revision.
- `raw/dataset-metadata.json`: dataset revision metadata.
- `snapshot.json`: source URLs, revision, row count, and local SHA-256 hashes.
- `dev-catalog.json`: patch-free screening metrics for all 23 dev instances.
- `pilot-selection.json`: three selected instances plus one fallback.
- `pilot-agent-inputs.json`: sanitized Agent inputs containing issue text and
  base revision, with all gold, tests, hints, and evaluation test IDs removed.
- `repo-snapshots.json`: exact checked-out HEADs, tracked sizes, clean-state
  checks, and upstream license hashes for the three selected repositories.
- `repos/`: ignored shallow checkouts pinned to selected base commits.
- `../../configs/integrations/swe-bench-lite-marshmallow-local-calibration.json`:
  versioned hashes and return codes from the first local evaluator calibration.
- `../../configs/integrations/swe-bench-lite-astroid-local-calibration.json`:
  versioned hashes and return codes from the second, distinct-repository local
  evaluator calibration.

Regenerate the patch-free catalog and selection:

```bash
python tools/select_swe_bench_lite_pilot.py
```

Selected pilot:

1. `marshmallow-code__marshmallow-1343` — real-issue calibration.
2. `pylint-dev__astroid-1196` — semantic inference and exception behavior.
3. `sqlfluff__sqlfluff-1763` — filesystem safety under encoding failures.

Fallback: `pydicom__pydicom-1139`.

The local calibration runner has verified two distinct tasks under isolated
Python 3.8.20 environments. For `marshmallow-code__marshmallow-1343`, the
baseline fails one FAIL_TO_PASS test and passes all 24 PASS_TO_PASS tests. For
`pylint-dev__astroid-1196`, the baseline fails two FAIL_TO_PASS tests and passes
all 24 PASS_TO_PASS tests. Both reference patches pass both groups. Calibration
builds exact Git Archive snapshots, applies evaluator patches outside the agent
boundary, and stores only hashes and return codes as versioned evidence.

```bash
python tools/calibrate_swe_bench_lite.py \
  --benchmark-root benchmarks/swe_bench_lite \
  --instance-id pylint-dev__astroid-1196 \
  --python /path/to/python-3.8-venv/bin/python \
  --output-dir .port_sessions/swe-bench-lite-astroid-calibration
```

This is a local compatibility calibration, not an agent score. Docker was
unavailable, so official Harness execution and the remaining historical
environments are still pending.

The first two real `deepseek-v4-flash` Dev Episodes are also recorded. The
unguided run made no change and failed the regression test. The environment-aware
run changed only `src/marshmallow/schema.py` and passed the one FAIL_TO_PASS plus
all 24 PASS_TO_PASS tests, but exhausted its 20-turn budget and therefore failed
the termination hard gate. It is retained as a Dev bad case, not Gold SFT data.
The versioned comparison contains hashes and metrics only; evaluator assets and
raw trajectories remain ignored local artifacts.

Astroid was then run once and retried after fixing an infrastructure defect.
The first attempt exposed that the new `runtime_guidance` event was missing
from the trajectory schema; it is retained only as an infrastructure regression
case. The schema-valid retry preserved all 24 PASS_TO_PASS tests but failed both
FAIL_TO_PASS tests and exhausted 30 turns despite recording the completion
reminder. It is a Dev bad case, not Gold data. Versioned hashes and admission
decisions are stored in
`../../configs/integrations/swe-bench-lite-astroid-deepseek-rollouts.json`.
