# Read-to-edit Constraint Repair Admissible-task Protocol

Status: **preregistered after v1 calibration closure and before new task acquisition or model calls**
Protocol date: 2026-08-20
Protocol version: `read-to-edit-constraint-repair-admissible.v1`

## 1. Why this is a new protocol

The original `read-to-edit-constraint-repair-ablation.v1` protocol admitted no task.
Its two frozen rows contained truncated parameterized pytest Node IDs. Conservative
expansion made test selection executable but caused target and regression groups to
overlap at the full-function level. No model was called and no intervention outcome
was observed.

Those tasks remain closed and are not replaced inside the original protocol. This
new protocol changes only the pre-model task-selection rule to exclude visibly
truncated parameterized IDs. It does not reinterpret or overwrite the failed
calibrations.

## 2. Frozen selection rule

Start from the same pinned Dev snapshot and exclude all 15 rows already in the
expanded Pilot at the time of this registration. Keep rows that satisfy all of:

1. one Oracle implementation file;
2. exactly one FAIL_TO_PASS test;
3. 1-100 PASS_TO_PASS tests;
4. `light` or `moderate` environment class;
5. no FAIL_TO_PASS or PASS_TO_PASS Node ID whose parameter suffix has more `[` than
   `]` characters.

Sort by Oracle patch changed lines descending, then `instance_id` ascending. Take
the first two rows from different repositories. The deterministic result is:

1. `pylint-dev__astroid-1268`: 4 changed lines, 1 target, 91 regressions;
2. `pydicom__pydicom-1694`: 2 changed lines, 1 target, 26 regressions.

Oracle content and hidden tests remain private; only path allowlists and aggregate
selection metadata may reach Runtime configuration.

## 3. Arms and order

1. Astroid-1268: Control, then Repair;
2. pydicom-1694: Repair, then Control.

Each task-arm pair has one valid Episode maximum, with no quality retry. The order
is counterbalanced and cannot change after acquisition or calibration begins.

## 4. Admission and shared policy

Before model calls, require exact Base Commit, clean repository, pinned environment,
baseline target failure, baseline regression pass, and Oracle target/regression pass.
An admission failure is not replaced.

Both arms use the configuration frozen in the original protocol:

- `deepseek-v4-flash`, temperature 0, thinking disabled, 4096 response tokens;
- maximum 18 turns, Deadline 4, Escalation delay 2;
- forced direct mutation after Escalation and one allowlisted target read;
- Post-edit Notice, Reminder 4, Critical 1, forced final response;
- 180-second evaluator timeout and `verifier-policy.v2`.

The only arm difference remains:

- Control: `implementation_constraint_repair_attempts=0`;
- Repair: `implementation_constraint_repair_attempts=1`.

## 5. Evidence and decision rules

Use the layered H1/H2/H3 interpretation in
`docs/roadmap/read-to-edit-constraint-repair-design.md`. In particular:

- no post-read violation means the mechanism was not activated;
- correction followed by an allowlisted edit supports action-selection recovery;
- only all-hard-gate success supports task-level recovery;
- another forbidden action, an incorrect edit, or identical hard outcomes does not
  justify changing the default;
- any extra read dispatch, second correction, scope/process regression, or hidden
  information exposure invalidates the mechanism.

Analyze only after all admitted pairs are archived. Two tasks support descriptive
paired evidence only, not statistical significance.

## 6. Claim boundary

This protocol is a local Dev mechanism ablation created after a calibration-only
closure with zero model calls. It is not a retry of failed model outcomes, an
official SWE-bench score, a training result, or a LoRA readiness gate.
