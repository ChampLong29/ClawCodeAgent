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

Regenerate the patch-free catalog and selection:

```bash
python tools/select_swe_bench_lite_pilot.py
```

Selected pilot:

1. `marshmallow-code__marshmallow-1343` — real-issue calibration.
2. `pylint-dev__astroid-1196` — semantic inference and exception behavior.
3. `sqlfluff__sqlfluff-1763` — filesystem safety under encoding failures.

Fallback: `pydicom__pydicom-1139`.

The official SWE-bench harness requires Docker. Docker was not available when
this snapshot was curated, so repository and dataset integrity were verified
locally, but official FAIL_TO_PASS/PASS_TO_PASS execution remains pending.
