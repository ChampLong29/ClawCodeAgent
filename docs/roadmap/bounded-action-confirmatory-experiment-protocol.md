# Bounded-Action Confirmatory Experiment Protocol

Status: **preregistered before repository acquisition, calibration, or model calls**
Protocol date: 2026-08-20
Protocol version: `bounded-action-confirmatory.v1`

## 1. Research question

After a model has made a scoped implementation edit, does a general post-edit
contract notice improve correctness on an unseen repository issue without
reducing regression, Diff Scope, permission, or termination compliance?

This is a confirmatory follow-up to the exploratory Marshmallow-1359 bad case.
That issue is excluded from further sampling. Its concrete implementation and
hidden test must not be included in either arm's Agent context.

## 2. Confirmatory task and admission gate

The next task is `pyvista__pyvista-4315`, the only not-yet-run SWE-bench Lite
Dev candidate in the pinned snapshot that adds a new repository family. It is
classified as `heavy-vtk`, has one target test, 114 selected regressions, an MIT
license, and a one-file Oracle scope.

Before any model call:

1. acquire the exact Base Commit recorded in the pinned dataset;
2. record repository HEAD, clean state, tracked size, and license hash;
3. build a pinned isolated Python environment;
4. require baseline FAIL_TO_PASS to fail and PASS_TO_PASS to pass;
5. require the reference patch to make both groups pass.

If any admission condition fails, stop and record an environment/calibration
result. Do not evaluate the model and do not substitute a previously observed
task after seeing the failure.

## 3. Fixed two-arm design

Both arms start from independent clean archives of the same Base Commit. Run
Control first and Treatment second, without inspecting or changing prompts,
thresholds, dependencies, or code between arms.

Shared configuration:

- model: `deepseek-v4-flash` through the configured Anthropic-compatible API;
- temperature: `0`;
- explicit thinking: `disabled`;
- maximum response tokens: `4096`;
- maximum model turns: `18`;
- implementation Deadline: turn `4`;
- implementation Escalation delay: `2` turns;
- Escalation action constraint: expose only direct edit tools and require a
  tool call until an allowed implementation path is edited;
- completion Reminder: `4` remaining turns;
- completion Critical threshold: `1` remaining turn;
- Critical action constraint: expose no tools and require final response;
- allowed implementation paths: the Oracle-derived path set, never the Oracle
  content;
- evaluator timeout: `180` seconds;
- same pinned interpreter and dependency lock for both arms.

The sole treatment variable is:

- **Control:** post-edit contract notice disabled.
- **Treatment:** post-edit contract notice enabled, including the frozen general
  instruction to identify the configuration owner/root or parent chain and to
  verify owner-to-leaf propagation of an explicit non-default value.

## 4. Outcomes

Primary success is conjunctive:

- every FAIL_TO_PASS test passes;
- every PASS_TO_PASS test passes;
- Diff Scope passes;
- process/permission checks pass;
- format checks pass;
- termination compliance passes.

Secondary descriptive outcomes:

- first target-path inspection turn;
- Deadline, Escalation, first direct edit, post-edit notice, Reminder, and
  Critical turns;
- target-to-edit lag and pre-edit investigation ratio;
- model/tool turns, failed tools, input/output tokens, and latency;
- changed paths and bad-case category.

With one task and one run per arm, results are descriptive. No statistical
significance, benchmark improvement, or model-level causal claim is permitted.

## 5. Stopping, retries, and analysis rules

- Exactly one valid model-quality Episode per arm; no quality retry or best-of-N.
- Do not inspect Control's candidate or verification details before Treatment
  finishes. Automation may check only whether execution reached an archived
  verification record.
- Provider transport retries already implemented by the Runtime are part of the
  fixed protocol and are not new Episodes.
- A failure before the first model request is infrastructure-only and ends the
  experiment unless the missing dependency can be fixed without changing task,
  model, policy, or evaluator semantics.
- A failure after a model request is preserved. Do not edit the prompt or rerun
  that arm.
- If an infrastructure defect invalidates one arm, document it and do not make a
  paired-effect claim. Any later rerun requires a new protocol version.
- Automated hard signals take precedence over Reviewer opinion.
- Analyze both arms only after both archived records exist.

## 6. Interpretation matrix

| Control | Treatment | Permitted interpretation |
|---|---|---|
| fail | pass | One-task evidence consistent with benefit from the frozen post-edit notice; replication still required. |
| pass | pass | No demonstrated correctness gain; compare cost and behavior only. |
| pass | fail | Evidence of possible harm or instability; do not enable by default based on this experiment. |
| fail | fail | No correctness benefit observed; compare failure categories to refine the next exploratory hypothesis. |

Official SWE-bench Harness execution remains separate. All results from this
protocol are local Dev evidence only.
