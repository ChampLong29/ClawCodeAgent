# Resource-bounded Harness Comparison

## 1. Research question

The comparison is not designed to reproduce a full SWE-bench leaderboard or to
claim that Claw is universally better than DeepSeek Harness. It asks a narrower,
falsifiable question:

> With DeepSeek-V4-Flash, task inputs, container images, inference effort, and
> budgets held fixed, do Claw runtime controls improve the trade-off among raw
> resolution, policy-compliant resolution, and execution cost?

SWE-bench `Resolved` remains visible in every report. The additional policy and
efficiency metrics do not replace it; they show costs and invalid successes that a
single aggregate score cannot express.

## 2. Alternatives considered

Three experiment directions were compared before selecting the combined design.

1. **Claw-only ablation.** This gives the cleanest causal attribution but can be
   dismissed as a system comparing only against itself.
2. **Under-measured operational properties.** Runtime enforcement, recovery,
   environment attribution, and auditability fit the project, but a custom task and
   custom metric can become self-confirming.
3. **Selected metrics against DeepSeek Harness.** An external baseline is stronger,
   but selecting only metrics on which Claw is expected to win is cherry-picking,
   and a comparison of two fully different stacks cannot identify the cause.

The adopted design combines their strengths: an external reference, a Claw parity
baseline, and an internal controlled ablation. The research claim concerns the
Pareto trade-off, not framework supremacy.

## 3. Frozen arms

### DSH Minimal

Use a pinned DeepSeek Harness commit and its `sdk-minimal` composition: persistent
shell plus `str_replace_editor`. Run it inside the same disposable SWE-bench
container boundary used by Claw. Record the exact profile tree and session JSONL.

### Claw Minimal

Expose the closest practical shell/editor surface with runtime guidance, forced
actions, path policy, recovery, and compaction disabled. Match the DSH system
prompt, model, inference effort, task text, wall-clock limit, turn limit, and token
budget. This arm detects adapter or Tool UX disadvantages before advanced controls
are credited.

### Claw Controlled

Change only preregistered controls from Claw Minimal: workspace/Diff policy,
Action Masking, bounded loop recovery, budget enforcement, immutable Trajectory,
and independent Verification. Each control must also be removable for focused
ablation; enabling the entire stack alone cannot identify a causal mechanism.

DeepSeek Harness is plugin-extensible and has tool guards/restrictions. Results
therefore compare the pinned `sdk-minimal` profile, not every configuration the
framework could implement.

## 4. Metrics fixed before model calls

### Primary

- **Policy-compliant Resolved:** tests pass, the patch applies, mutation remains in
  the common allowlist, hidden evaluator assets remain inaccessible, and the run
  stays within the common budget.
- **Budgeted Resolved:** raw resolved tasks achieved under the same token, turn,
  tool-call, wall-clock, and inference-effort limits.

### Required counter-metrics

- Raw SWE-bench Resolved, including successes later rejected by policy.
- Input/cache/output tokens, tool calls, elapsed time, and estimated API cost per
  attempted and per valid resolved task.

### Diagnostic

- Empty or unappliable patches, off-target mutation, repeated successful reads,
  repeated failed actions, first edit/test turn, recovery after a Tool Error,
  termination reason, and infrastructure-versus-model failure classification.
- Evidence completeness: model-requested, dispatched, rejected, and completed tool
  calls remain distinguishable. This is an audit property, not a substitute for
  task success.

All metrics, allowlists, and denominators are frozen before viewing arm outcomes.
Both favorable and unfavorable trade-offs are reported. A safety gain that lowers
raw Resolved is not described as an unqualified improvement.

## 5. Sampling and repetition

The first stage uses a deterministic, repository-stratified frozen subset and one
fresh Episode per arm/task. Under a limited budget, additional independent tasks
usually provide more information than repeating every task.

Repetition is used for a specific variance question rather than by default:

- repeat all tasks whose paired arm outcomes disagree;
- repeat a preregistered random sample of agreeing outcomes;
- use fresh session IDs and workspaces, never quality-retry an Episode in place;
- retain the first-run table separately from stability estimates.

This separates a Harness effect from provider/trajectory variance without reporting
the best of several attempts. If the initial subset is too small for a stable rate,
the claim remains task-bounded and is supported by paired case analysis rather than
an unsupported population-level significance claim.

## 6. Admission and stopping rules

Before model calls, each task must demonstrate baseline target failure, baseline
regression pass, Oracle target pass, and Oracle regression pass in the selected
environment. An environment failure is preserved as evidence and repaired only in
a separately named compatibility layer.

Stop or redesign before the main comparison when:

- Claw Minimal is materially behind DSH Minimal because of protocol, prompt, or
  Tool UX defects;
- the arms do not receive equivalent task information or execution budgets;
- the policy cannot be applied to both outputs by one independent verifier;
- infrastructure errors dominate the valid task sample.

## 7. Evidence and claim boundary

Each result binds the task revision, Harness version/commit, profile/config hash,
model identity, effort, prompt, budgets, container identity, patch, trajectory,
verification report, and cost summary. Raw Episode stores remain append-only;
derived diagnostics receive versioned summaries.

Acceptable reporting is of the form:

> On frozen subset S under budget B, Claw Controlled produced X/N raw and Y/N
> policy-compliant resolutions versus the pinned DSH Minimal and Claw Minimal
> baselines, with the following token, time, and failure-recovery trade-offs.

The subset is not an official SWE-bench score. A disclosed dependency repair is a
compatibility evaluation, not an unmodified official result. No claim is made that
the compared DSH profile exhausts DeepSeek Harness capabilities.

## 8. Existing evidence that supports the protocol

- Versioned SWE-bench Lite selection and repository snapshots.
- Isolated Episodes, checkpoints, container execution, hidden-test staging, and
  Diff/permission/termination verification.
- Immutable Trajectory v2 plus behavior diagnostics that distinguish requested,
  dispatched, rejected, and completed actions.
- Frozen Deadline, bounded-action, progressive-action, post-edit-contract, and
  read-to-edit experiments, including archived negative and non-triggered results.
- Cross-model Tool UX v2 evidence: Qwen3-1.7B 0/10 and DeepSeek-V4-Flash 8/10 on the
  fixed local suite, recorded as complete-stack evidence rather than SWE-bench.
- SWE-bench v5 compatibility evaluation for `pvlib__pvlib-python-1854`: the
  unmodified published image failed before collection because of NumPy dependency
  drift; a disclosed `numpy<2` layer reproduced 1/1 FAIL_TO_PASS and 281/281
  PASS_TO_PASS success without changing the task, tests, or candidate patch.
