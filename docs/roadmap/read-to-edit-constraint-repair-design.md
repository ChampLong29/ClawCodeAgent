# Read-to-edit Constraint Repair Design

Status: **implemented and contract verified; no treatment Episode has run**
Design date: 2026-08-20
Mechanism version: `read-to-edit-constraint-repair.v1`

## 1. Question

When a model has consumed the single target-file read allowed after implementation
Escalation, can one corrective request recover a tool-selection violation into an
allowlisted direct edit without adding task information, expanding permissions, or
turning the run into an unbounded retry loop?

This mechanism targets protocol adherence after a read. It is not intended to make
an incorrect implementation correct, provide another inspection, or replace model
reasoning with a controller-authored patch.

## 2. Evidence that motivated the intervention

The immutable result is
`configs/integrations/swe-bench-lite-progressive-action-constraint-result.json`.
The relevant facts are:

| Evidence | What is directly observed | What may be inferred |
|---|---|---|
| Astroid Strict | First edit on turn 5; no Escalation; target test failed | This arm says nothing about read-to-edit repair because the treatment point was never reached |
| Astroid Progressive | Escalation on turn 6; target-read option offered but not consumed; direct edit on turn 7; same failed patch as Strict | Merely exposing a read option does not force its use and does not fix a semantic miss |
| pydicom Strict | Escalation on turn 6; `read_file` requested on edit-only turn 7; request rejected; no mutation | Immediate termination can cut off a potentially recoverable action-selection error |
| pydicom Progressive | Target read consumed on turn 7; next constraint was `required_direct_mutation`; turn 8 requested `read_file` again; request rejected; no mutation | One additional read extends the path by one turn, but the model still failed to transition from reading to editing |

The last row is the narrow causal motivation. Its Trajectory v2 contains, in order,
`implementation_escalation`, `implementation_target_read_consumed` with
`next_action_constraint=required_direct_mutation`, and then `runtime_stop` with
`requested_tool_names=["read_file"]`. The model request exposed only direct-edit
tools and used required tool choice. Therefore the next intervention should not be
described as “make edit-only clearer” or “allow another read”; the existing Runtime
already did the former, and the latter would change the investigation budget.

The evidence does **not** prove that a correction will produce a correct patch. It
only establishes a recoverable-looking boundary between a rejected model request
and a possible subsequent action.

## 3. Minimal intervention

Set both:

```text
implementation_target_read_allowance = 1
implementation_constraint_repair_attempts = 1
```

After the target read is consumed, edit-only mode remains unchanged. If the next
model response violates it:

1. do not dispatch or execute any requested tool;
2. record the violating model response and requested tool names;
3. append one Runtime correction stating that the request was not executed, no new
   file or test information is available, and the next action must edit an allowed
   implementation path;
4. expose only `write_file` and `edit_file` with required tool choice;
5. count the rejected request as a model/tool-bearing turn;
6. if the correction is violated, stop with
   `action_constraint_unsatisfied` and record that the repair allowance was used and
   exhausted.

The correction is available only after the one target read was actually consumed.
It cannot repair an off-target first read, mixed request, Strict-arm violation, or
unrelated permission/security rejection. The default remains zero, preserving all
historical behavior.

## 4. Hypotheses and falsification

### H1: recovery of action selection

The correction increases the fraction of treatment Episodes that perform an
allowlisted direct mutation after a first post-read edit-only violation.

H1 is not supported if the model repeats a forbidden action, returns no tool call,
or reaches the turn limit without a direct edit.

### H2: boundedness and safety

The correction dispatches no violating tool, provides no new task information,
allows at most one additional model request, and introduces no Diff Scope or process
violation.

Any dispatched rejected read, second correction, off-target edit, hidden-test
exposure, or unbounded model-call increase falsifies H2 and blocks model experiments.

### H3: task outcome

Among Episodes that recover into an edit, at least one passes FAIL_TO_PASS,
PASS_TO_PASS, Diff Scope, process, format, and termination gates together.

An edit without hard-gate success supports H1 at most, not H3. A target-test pass
with regression or scope failure also does not support H3.

## 5. Contract evidence before model calls

Focused deterministic tests cover two branches:

- a repeated read is rejected before dispatch, one correction is recorded with
  `new_task_information_provided=false`, and the simulated provider then edits;
- a simulated provider ignores the correction, receives no second correction, and
  stops with `constraint_repairs_used=1` and
  `constraint_repairs_exhausted=true`.

These tests establish only Runtime contract behavior. They are not model-quality or
benchmark evidence.

## 6. Experimental boundary

Do not rerun Astroid-1978 or pydicom-1256 to seek a better result. Select previously
unrun, independently calibrated Dev tasks and freeze Control/Treatment order before
the first model call. Control uses the existing Progressive policy with zero repair;
Treatment differs only by one repair attempt. Use at most one valid Episode per
task-arm pair and analyze after all admitted pairs are archived.

Primary reporting must separate:

- first violation and whether it was dispatched;
- correction offered, edit after correction, and repeated violation;
- target and regression tests;
- Diff/process/format/termination gates;
- tokens, latency, and extra model calls.

With a very small task set, report paired descriptive outcomes only. Do not claim
statistical significance, general model improvement, official SWE-bench performance,
or LoRA readiness.
