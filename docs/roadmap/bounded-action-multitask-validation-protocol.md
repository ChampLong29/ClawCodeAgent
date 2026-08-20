# Bounded-Action Multi-task Validation Protocol

Status: **preregistered before task-specific repository acquisition, calibration, or model calls**
Protocol date: 2026-08-20
Protocol version: `bounded-action-multitask.v1`

## 1. Research question

Can the frozen bounded-action Treatment policy produce reproducible local Dev
successes across more than one previously unrun Issue, while preserving
regressions, scoped edits, process compliance, termination compliance, and a
user-facing final response?

This is a robustness replication, not another Post-edit Notice ablation. The
PyVista Control/Treatment result remains the only paired Notice comparison.

## 2. Frozen task selection

Select exactly these two unrun rows from the pinned SWE-bench Lite Dev snapshot:

1. `pvlib__pvlib-python-1606`: one-file numerical-search boundary change, one
   FAIL_TO_PASS and ten PASS_TO_PASS tests.
2. `sqlfluff__sqlfluff-1733`: one-file formatter interaction change, one
   FAIL_TO_PASS and three PASS_TO_PASS tests.

Both rows have non-empty regression groups, fit the existing maximum of two
tasks per repository, and contrast scientific numerical behavior with formatter
state interaction. No task may be replaced after observing admission or model
outcomes.

## 3. Admission gate

For each task, before its first model call:

1. acquire the exact Base Commit from the pinned snapshot and record repository
   identity, clean state, tracked size, and license hash;
2. build or reuse a pinned isolated Python environment and save its lock;
3. require baseline FAIL_TO_PASS to fail and PASS_TO_PASS to pass;
4. require the reference patch to make both groups pass.

A pre-model failure is infrastructure/calibration evidence and consumes no model
Episode. Dependency repair is allowed only when it does not change the task,
tests, model policy, or evaluator semantics.

## 4. Fixed Agent policy

Run pvlib first and SQLFluff second. Both use:

- model `deepseek-v4-flash`, temperature `0`, explicit thinking `disabled`;
- maximum response tokens `4096` and maximum model turns `18`;
- implementation Deadline at turn `4`, Escalation after `2` more turns;
- Escalation exposes only direct edit tools and requires a tool call until an
  Oracle-derived allowed implementation path is edited;
- Post-edit Contract Notice enabled;
- completion Reminder with `4` turns remaining and Critical with `1` turn;
- Critical exposes no tools and requires a final response;
- evaluator timeout `180` seconds;
- Verifier Policy `verifier-policy.v2`.

Only allowed path patterns and public task text enter Agent context. Oracle and
hidden test content remain evaluation-only.

## 5. Outcomes and stopping rule

Primary success is conjunctive FAIL_TO_PASS, PASS_TO_PASS, Diff Scope,
process/permission, format, and termination compliance. Record the zero-weight
`final_response_quality.v1` Soft signal separately.

Secondary outcomes are model/tool turns, target localization and edit timing,
guidance injection turns, failed tools, tokens, latency, changed paths, and
bad-case category.

- At most one valid model-quality Episode per task; no best-of-N or quality retry.
- Provider transport retries inside one Episode remain part of Runtime behavior.
- Preserve any post-model failure and do not change the policy between tasks.
- Analyze aggregate outcomes only after both tasks have archived records.
- With two tasks, report counts and distributions only. Do not claim statistical
  significance, official SWE-bench performance, or model-level improvement.
