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
- `pilot-selection.json`: nine selected Dev instances across six repositories.
- `pilot-agent-inputs.json`: sanitized Agent inputs containing issue text and
  base revision, with all gold, tests, hints, and evaluation test IDs removed.
- `repo-snapshots.json`: exact checked-out HEADs, tracked sizes, clean-state
  checks, and upstream license hashes for the nine pinned task snapshots.
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
2. `marshmallow-code__marshmallow-1359` — fresh cross-Issue replication of
   bounded actions and nested configuration inheritance.
3. `pylint-dev__astroid-1196` — semantic inference and exception behavior.
4. `sqlfluff__sqlfluff-1763` — filesystem safety under encoding failures.
5. `pydicom__pydicom-1139` — Python iteration and containment protocol behavior;
   promoted from the original fallback for fresh Deadline-Guidance validation.
6. `pvlib__pvlib-python-1707` — scientific numerical boundary behavior for
   incidence angles, selected for Deadline-plus-Escalation validation.
7. `pydicom__pydicom-1413` — within-family bytes-versus-MultiValue contract
   generalization for path-scoped Post-edit Notice validation.
8. `pylint-dev__astroid-1333` — namespace-package path resolution with 46
   regression tests and a fresh path-scoped guidance observation.
9. `pyvista__pyvista-4315` — preregistered new-family comparison with one
   target test, 114 regressions, and an explicit heavy-VTK admission gate.
10. `pvlib__pvlib-python-1606` — golden-section equal-bound behavior with one
    target test and 10 selected regressions.
11. `sqlfluff__sqlfluff-1733` — interacting formatter rules with one target
    test and three selected regressions.
12. `pylint-dev__astroid-1978` — NumPy deprecation behavior with one target,
    twelve regressions, and Strict-first paired action-constraint evaluation.
13. `pydicom__pydicom-1256` — nested BulkDataURI handler propagation with one
    target, twenty-two regressions, and Progressive-first paired evaluation.

The local calibration runner has verified thirteen tasks from six repositories under isolated
historical environments; the newest two have calibration evidence but no model Episode yet. For `marshmallow-code__marshmallow-1343`, the
baseline fails one FAIL_TO_PASS test and passes all 24 PASS_TO_PASS tests. For
`marshmallow-code__marshmallow-1359`, the baseline fails one target test and
passes all 76 regressions; its reference patch passes both groups. For
`pylint-dev__astroid-1196`, the baseline fails two FAIL_TO_PASS tests and passes
all 24 PASS_TO_PASS tests. SQLFluff and pydicom add filesystem-safety and Python
protocol coverage; pvlib adds a pinned NumPy/Pandas/SciPy environment and 30
PASS_TO_PASS tests. The second pydicom task adds 301 PASS_TO_PASS tests for
bytes/container semantics; the second Astroid task adds 46 path-resolution
regressions. PyVista adds a pinned Python 3.8.20, VTK 9.2.6, and NumPy 1.24.4
environment with 114 regressions. The two multi-task additions contribute 10 and
three regressions respectively; the paired-ablation additions contribute 12 and
22. All thirteen reference patches pass both groups. Calibration
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
unavailable, so official Harness execution remains pending. The selected
PyVista heavy-VTK task is calibrated only in the pinned local WSL environment.

The first two real `deepseek-v4-flash` Dev Episodes are also recorded. The
unguided run made no change and failed the regression test. The environment-aware
run changed only `src/marshmallow/schema.py` and passed the one FAIL_TO_PASS plus
all 24 PASS_TO_PASS tests, but exhausted its 20-turn budget and therefore failed
the termination hard gate. It is retained as a Dev bad case, not Gold SFT data.
The versioned comparison contains hashes and metrics only; evaluator assets and
raw trajectories remain ignored local artifacts.

On 2026-08-20 the same calibrated Marshmallow Dev task produced the first
locally compliant real-repository Episode under a bounded-action configuration.
DeepSeek explicit thinking was disabled, each response was capped at 4096
tokens, and optional edit/final-response constraints were configured. The model
localized the target on turn 2, edited the allowed source path on turn 7, and
completed after 15 tool-bearing turns. The one FAIL_TO_PASS test, all 24
PASS_TO_PASS tests, Diff Scope, process, format, and termination gates passed.
The model completed before either forced constraint triggered, so the run is not
a causal estimate of those constraints and remains a local Dev result rather
than an official SWE-bench score. Versioned hashes and the exact claim boundary
are stored in
`../../configs/integrations/swe-bench-lite-marshmallow-bounded-action-success.json`.

The policy was then replicated on the newly selected Marshmallow-1359 issue.
Both attempts changed only `src/marshmallow/fields.py`, passed all 76 selected
regressions, and terminated normally, but failed the target test. In v2 the
escalation request actually constrained the next model action to an edit. The
candidate still replaced missing immediate-parent options with `None` instead
of following the existing `root` parent chain, so `Schema.Meta.datetimeformat`
did not reach nested DateTime fields. This is evidence that the action mechanism
executed, not that it improved solution quality. The issue will not be sampled
again; hashes, both failures, and the admission decision are stored in
`../../configs/integrations/swe-bench-lite-marshmallow-1359-bounded-action-rollouts.json`.

PyVista-4315 was run under a protocol hashed before repository acquisition,
calibration, or model calls. Initial calibration exposed missing `ipykernel`,
`tqdm`, and `meshio`; after pinning them, admission reached baseline target
failure with 114 regressions passing and reference success for both groups.
The preregistered Control disabled Post-edit Contract Guidance and the Treatment
enabled it; all other model, budget, action, and evaluator settings were equal.
Both arms passed the target test, all 114 regressions, Diff Scope, process,
format, and termination gates. The Treatment notice was injected, but because
Control also succeeded there is no demonstrated correctness gain. Protocol,
environment lock, calibration history, and paired evidence are stored under
`../../docs/roadmap/bounded-action-confirmatory-experiment-protocol.md` and
`../../configs/integrations/swe-bench-lite-pyvista-4315-confirmatory-comparison.json`.

A subsequent protocol froze pvlib-1606 and SQLFluff-1733 before task-specific
acquisition, calibration, or model calls. Both passed local admission. Under the
same bounded Treatment policy, pvlib passed its target and all ten regressions.
SQLFluff preserved all three regressions but failed its target: on turn 7 it
first requested the correct L039 path, yet used `read_file` during a
direct-edit-only request, so Runtime stopped without dispatching or mutating.
The 1/2 result is retained as a strategy tradeoff, not retried. Behavior
diagnostics v4 distinguishes model-requested, dispatched, and rejected calls;
the Bad Case classifier separates this action-constraint failure from
infrastructure errors. Evidence is stored in
`../../configs/integrations/swe-bench-lite-bounded-action-multitask-result.json`.

Astroid was then run once and retried after fixing an infrastructure defect.
The first attempt exposed that the new `runtime_guidance` event was missing
from the trajectory schema; it is retained only as an infrastructure regression
case. The schema-valid retry preserved all 24 PASS_TO_PASS tests but failed both
FAIL_TO_PASS tests and exhausted 30 turns despite recording the completion
reminder. It is a Dev bad case, not Gold data. Versioned hashes and admission
decisions are stored in
`../../configs/integrations/swe-bench-lite-astroid-deepseek-rollouts.json`.
