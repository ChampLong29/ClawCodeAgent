# Post-edit Container/Return-type Contract Fresh Validation Protocol

Status: **preregistered before task acquisition, calibration, or model calls**
Protocol date: 2026-08-22
Protocol version: `post-edit-container-return-type-fresh-validation.v1`

## 1. Purpose

The path-scoped Post-edit Contract Notice (default enabled) asks the model, after its
first successful edit on an Oracle-derived implementation path, to run a minimal target
verification plus one related regression and to check scalar/collection, container and
return type, shape, ordering, null, and metadata beyond plain values or exceptions.

pvlib-1606 produced the motivating Bad Case: the model fixed the numeric boundary but
silently converted a pandas Series result into an ndarray, and the return-type regression
failed. This protocol validates the notice on a **fresh** Dev issue whose Oracle and
hidden test require a pandas Series return — the same container/return-type failure class —
to test whether the notice is sufficient to close that class on a previously unseen task.

This is a single-arm mechanism-sufficiency check on one fresh task, not a Control/Treatment
comparison (the frozen PyVista-4315 confirmatory experiment already reported
`control_pass_treatment_pass` for the notice's general effect). It is not a retry of
pvlib-1606 or any other previously run task.

## 2. Frozen selection rule

Start from the same pinned Dev snapshot
(`69611d31007e1c6731db8bd5b5c3f2d33f5bab6e`) and exclude all 17 rows in the expanded
Pilot and the 2 rows closed by truncated parameterized Node IDs
(`sqlfluff__sqlfluff-1517`, `pylint-dev__astroid-1866`). Keep rows that satisfy all of:

1. one Oracle implementation file;
2. exactly one FAIL_TO_PASS test;
3. 1-100 PASS_TO_PASS tests;
4. `light`, `moderate`, or `scientific` environment class (pvlib environments are
   already admitted twice in the Pilot; the container/return-type class concentrates in
   pandas-heavy scientific repositories);
5. container/return-type semantics: the Oracle patch introduces a pandas/numpy
   type-preserving computation (e.g. `to_series()`, `.diff()`, `.dt.`, `.iloc`,
   `Series(`, `np.asarray`) AND the FAIL_TO_PASS test asserts the container type or
   index (`assert_series_equal`, dtype, index, or Series instance assertions).

Sort by Oracle patch changed lines descending, then `instance_id` ascending. Take the
first row. The deterministic result is:

- `pvlib__pvlib-python-1072`: `pvlib/temperature.py`, 5 changed lines, 1 target
  (`pvlib/tests/test_temperature.py::test_fuentes_timezone[Etc/GMT+5]`), 18 regressions.
  The Oracle rewrites the internal timedelta computation from a numpy `np.diff` array to
  a pandas Series (`index.to_series().diff().dt.total_seconds()` with `.iloc`
  assignment); the hidden test uses `assert_series_equal(out, pd.Series(...))`.

Oracle content and hidden tests remain private; only the path allowlist
(`pvlib/temperature.py`) and aggregate selection metadata may reach Runtime
configuration.

## 3. Arm and frozen policy

Single arm with the Post-edit Contract Notice **enabled** (the mechanism under test),
path-scoped to the Oracle implementation path:

- `deepseek-v4-flash`, temperature 0, thinking disabled, 4096 response tokens;
- maximum 18 turns, Deadline 4, Escalation delay 2;
- forced direct mutation after Escalation; target-read allowance 0 (strict default;
  this experiment does not involve the read-to-edit repair mechanism);
- implementation constraint repair attempts 0 (repair stays disabled by default);
- Post-edit Contract Notice enabled, Reminder 4, Critical 1, forced final response;
- 180-second evaluator timeout and `verifier-policy.v2`;
- `--allow-path pvlib/temperature.py`, prompt version
  `swe-bench-lite-dev.deepseek-v4-flash.v1`.

## 4. Admission gate

Before model calls, require: exact Base Commit
`04a523fafbd61bc2e49420963b84ed8e2bd1b3cf`, clean repository, pinned Python 3.8.20
environment (pytest, pytest-mock, numpy, pandas) whose environment contract passes, and
the four calibration states: baseline target FAILS, baseline regressions PASS, Oracle
target PASSES, Oracle regressions PASS. An admission failure is recorded and not
replaced; no model is called before the gate.

## 5. Hypotheses and decision rules

- H1 mechanism activation: the Post-edit Notice is delivered after the first successful
  edit on `pvlib/temperature.py`.
- H2 contract verification: after the notice, the model performs at least one
  verification action targeting the container/return-type contract (running the target
  test, a type/index probe, or a related regression) before the final response.
- H3 hard success: target, all 18 regressions, Diff scope, permission, format,
  termination, and final-response-quality gates all pass.

Decision rules:

- H3 passes: descriptive single-sample support that the path-scoped notice plus its
  contract checklist suffices on this fresh issue. This is mechanism sufficiency, not a
  causal claim about model improvement.
- H3 fails with a container/return-type miss: the failure class persists on an unseen
  task; the Episode is archived as a Dev Bad Case. No same-task retry or prompt tuning.
- H1 not observed (notice not delivered after the first allowed-path edit): record as an
  infrastructure deviation and do not interpret the Episode as evidence about the notice.

## 6. Evidence and claim boundary

Record the frozen protocol hash, calibration hashes, Episode trajectory events, Verifier
v2 signals, and the H1/H2/H3 classification in machine-readable evidence under
`configs/integrations/`, and append the observations, inference, and alternatives to
`docs/roadmap/swe-bench-lite-experiment-log.md` before the commit.

This is local Dev mechanism evidence only: not an official SWE-bench score, not a model
capability estimate, not a training result, and not a LoRA readiness gate.
