# Read-to-edit Constraint Repair Ablation Protocol

Status: **preregistered before task repository acquisition, calibration, or model calls**
Protocol date: 2026-08-20
Protocol version: `read-to-edit-constraint-repair-ablation.v1`

## 1. Research question

On previously unrun SWE-bench Lite Dev issues, can one no-new-information
corrective request recover a model that violates edit-only mode after consuming its
single target-file read, without weakening task, regression, Diff Scope, process,
format, or termination gates?

The mechanism rationale and source evidence are frozen separately in
`docs/roadmap/read-to-edit-constraint-repair-design.md`.

## 2. Deterministic task selection

Start from the pinned Dev snapshot and exclude every row already in the 13-task
Pilot. Keep rows with exactly one Oracle implementation file, exactly one
FAIL_TO_PASS test, 1-50 PASS_TO_PASS tests, and a `light` or `moderate` environment
class. Sort by Oracle patch changed lines descending, then `instance_id` ascending,
and take the first two rows from different repositories.

This produces:

1. `sqlfluff__sqlfluff-1517`: 11 changed Oracle lines, 1 target, 42 regressions;
2. `pylint-dev__astroid-1866`: 6 changed Oracle lines, 1 target, 10 regressions.

Oracle metadata is used only for selection, the implementation-path allowlist, and
private verification. Patch content and hidden tests must not enter Agent messages.
No selected task may be replaced after repository acquisition or calibration begins.

## 3. Counterbalanced arm order

1. SQLFluff-1517: Control, then Repair;
2. Astroid-1866: Repair, then Control.

The reverse order limits a shared run-order confound. Each task-arm pair may produce
at most one valid Episode; there are four maximum and no quality retries.

## 4. Admission before model calls

For each task, before its first model call:

1. acquire and verify the exact Base Commit;
2. record clean state, object availability, license, and repository identity;
3. build or reuse a pinned isolated Python environment;
4. require baseline FAIL_TO_PASS to fail and all selected PASS_TO_PASS tests to pass;
5. require the Oracle patch to make both groups pass.

Pre-model dependency repair is allowed only when it does not change task, test,
evaluator, Agent, or arm semantics. A task that fails admission remains an
infrastructure/calibration result and is not replaced.

## 5. Shared configuration and sole treatment difference

Both arms use `deepseek-v4-flash`, temperature 0, thinking disabled, maximum 4096
response tokens, 18 model turns, Deadline at turn 4, Escalation after 2 further
turns, one target read after Escalation, Post-edit Contract Notice, Completion
Reminder at 4 remaining turns, Critical at 1, forced final response, a 180-second
evaluator timeout, and `verifier-policy.v2`.

Both arms reject non-allowlisted reads/edits before dispatch. After the one target
read, both expose only `write_file` and `edit_file` with required tool choice.

The only difference is:

- **Control:** `implementation_constraint_repair_attempts = 0`;
- **Repair:** `implementation_constraint_repair_attempts = 1`.

The Repair arm may intervene only after the target read was consumed and the next
edit-only response was invalid. It executes no violating tool, adds no task
information, consumes one model/tool-bearing turn, and provides no second repair.

## 6. Outcomes and interpretation

Primary task success requires FAIL_TO_PASS, PASS_TO_PASS, Diff Scope,
process/permission, format, and termination gates together. Separately record:

- whether Escalation, target-read consumption, and a first edit-only violation occur;
- whether a correction is offered and whether the next response directly edits;
- violating requested tools versus dispatched tools;
- repeated violation and repair exhaustion;
- first localization/edit turns, tokens, latency, and final-response quality.

Interpret outcomes in layers:

1. If neither arm reaches the post-read violation point, the pair is mechanism-not-
   activated and says nothing about repair efficacy.
2. If Repair converts a rejected request into an allowlisted edit, it supports
   action-selection recovery only.
3. Only a recovered edit that passes every hard gate supports task-level recovery.
4. Any extra read dispatch, second correction, scope/process regression, or hidden
   information exposure invalidates the Repair mechanism for later use.
5. If Repair merely delays the same stop or produces an incorrect edit, do not change
   the default from zero.

Analyze only after all admitted pairs are archived. With two tasks, report
descriptive paired outcomes and no statistical significance.

## 7. Claim boundary

This is a local Dev mechanism ablation, not an official SWE-bench score, general
model-level improvement estimate, training result, or LoRA readiness gate. Contract
tests prove only Runtime behavior; model Episodes are required for empirical claims.
