# Progressive Action Constraint Ablation Protocol

Status: **preregistered before task repository acquisition, calibration, or model calls**
Protocol date: 2026-08-20
Protocol version: `progressive-action-constraint-ablation.v1`

## 1. Research question

On previously unrun SWE-bench Lite Dev issues, does allowing one allowlisted
target-file read after implementation Escalation reduce avoidable action-constraint
termination relative to the strict edit-only policy, without weakening test,
regression, Diff Scope, process, format, or termination gates?

This is a paired mechanism ablation. It does not estimate official SWE-bench
performance or LoRA value.

## 2. Frozen tasks and arm order

Use exactly two unrun rows from the pinned Dev snapshot:

1. `pylint-dev__astroid-1978`, then run Strict followed by Progressive;
2. `pydicom__pydicom-1256`, then run Progressive followed by Strict.

The counterbalanced order limits a shared time/order confound. Both tasks have one
implementation file, one FAIL_TO_PASS test, non-empty PASS_TO_PASS groups, and
different semantic domains. No task or order may be replaced after admission or
model outcomes are observed.

## 3. Admission gate

Before the first model call for each task:

1. acquire its exact Base Commit and record repository identity and clean state;
2. build or reuse a pinned isolated Python environment and save its lock;
3. require baseline FAIL_TO_PASS to fail and all selected PASS_TO_PASS tests to pass;
4. require the reference patch to make both groups pass.

Pre-model dependency repair is allowed only when task, tests, evaluator semantics,
and Agent policy remain unchanged. A task that cannot pass admission is reported as
an infrastructure/calibration result and is not replaced.

## 4. Shared policy and only treatment difference

Both arms use model `deepseek-v4-flash`, temperature `0`, explicit thinking
`disabled`, maximum response tokens `4096`, maximum model turns `18`, Deadline at
turn `4`, Escalation after `2` more turns, Post-edit Contract Notice enabled,
Completion Reminder at `4` remaining turns, Critical at `1`, forced final response,
180-second evaluator timeout, and `verifier-policy.v2`.

Both arms require direct mutation after Escalation and restrict reads/edits to the
Oracle-derived implementation-path allowlist. The only difference is:

- **Strict:** `implementation_target_read_allowance = 0`;
- **Progressive:** `implementation_target_read_allowance = 1`.

After the Progressive arm consumes its one target read, its next tool-bearing
request is edit-only. Off-target reads, a second read, mixed calls, and off-target
edits stop before dispatch.

## 5. Outcomes and stopping rule

Primary task success is conjunctive FAIL_TO_PASS, PASS_TO_PASS, Diff Scope,
process/permission, format, and termination compliance. Separately record:

- action-constraint termination and whether a target read was accepted or rejected;
- target localization, first edit, guidance, and finalization turns;
- requested, dispatched, rejected, and failed tool calls;
- tokens, latency, changed paths, and `final_response_quality.v1`.

Run at most one valid Episode per task-arm pair: four maximum, no quality retry and
no best-of-N. Analyze arms only after all admitted pairs are archived. Report paired
task outcomes and descriptive costs only; with two tasks, do not claim statistical
significance or model-level improvement.

## 6. Decision rule

- Prefer Progressive for later data collection only if it eliminates at least one
  Strict action-constraint stop and introduces no new hard-gate regression.
- Keep Strict if Progressive creates any new scope/process violation without a
  compensating primary success.
- If neither policy reaches Escalation, or both arms have identical hard outcomes,
  record the result as inconclusive and do not tune on these tasks.
- Do not enter LoRA because of this ablation alone. Dataset freezing remains a
  separate later gate.
