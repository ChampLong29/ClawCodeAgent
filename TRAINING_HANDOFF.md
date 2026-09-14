# Training and Experiment Handoff

This document is the operational entry point for continuing the project on another
device. Treat the Git commit containing this file as the implementation baseline;
do not reuse the older `68b7ae0` generation commit for new Episodes.

## 1. Current evidence state

- The local coding-agent runtime, Trajectory v2, Verification v2, deterministic
  data governance, LlamaFactory export, Dry-run backend, and PEFT LoRA/QLoRA
  backend are implemented.
- SWE-bench Lite Dev is pinned at dataset revision
  `69611d31007e1c6731db8bd5b5c3f2d33f5bab6e`.
- The Pilot contains 20 selected tasks from six repositories. Eighteen pass the
  local baseline/Oracle admission gate. SQLFluff-1517 and Astroid-1866 remain
  recorded zero-model-call admission failures because truncated historical
  parameter IDs cannot isolate target and regression groups.
- The completed Strict/Progressive experiment has four valid Episodes and 0/4
  hard successes. Its paired outcome is inconclusive; the strict default remains.
- A one-attempt, no-new-information read-to-edit repair is implemented and
  contract verified. It is disabled by default.
- The follow-on Astroid-1268 and pydicom-1694 Control/Repair comparison is
  complete: four valid Episodes, 2/4 hard successes, and identical hard outcomes
  within both pairs. Repair never triggered, so the result is inconclusive and it
  remains disabled by default.
- The OCI boundary, official SWE-bench v5 runner/adapter, local Qwen3-1.7B vLLM
  configuration, repeated-read guard, and dispatch-time write allowlist are
  implemented with focused tests and versioned integration evidence.
- Tool UX v2 has a frozen ten-task complete-stack comparison: Qwen3-1.7B 0/10 and
  DeepSeek-V4-Flash 8/10. It is local suite evidence, not SWE-bench.
- `pvlib__pvlib-python-1854` reached 1/1 FAIL_TO_PASS and 281/281 PASS_TO_PASS under
  the official v5 Harness after a disclosed `numpy<2` compatibility layer. The
  unmodified published image failed before pytest collection because of NumPy 2
  dependency drift; neither result is reported as an aggregate official score.
- The resource-bounded comparison protocol separates DSH Minimal, Claw Minimal,
  and Claw Controlled and fixes policy-compliant resolution plus budgeted
  resolution as primary metrics.
- The seven-task Claw Base / Claw Enhanced / Pi Raw pilot has completed its first
  three tasks. Raw resolution is respectively Base 0/3, Enhanced 1/3, and Pi 1/3;
  every policy-compliant result is 0/3. The SQLFluff-1763 original Verifier
  regression failures are preserved but were traced to missing private `/dev/shm`;
  model-free clean verification passes all 66 regressions for every candidate.
- `configs/integrations/swe-bench-lite-runtime-environments-v1.json` is the
  portable source of truth for the seven task images, in-container interpreters,
  allowlists, and Pi image. Docker admission now runs inside the pinned image, so
  these pilot tasks do not require task-specific host virtualenv exports.
- No real LoRA/QLoRA training effect or aggregate official SWE-bench score is
  claimed.

The authoritative evidence files are:

- `configs/integrations/swe-bench-lite-progressive-action-constraint-result.json`
- `configs/integrations/swe-bench-lite-read-to-edit-repair-calibration-result.json`
- `configs/integrations/swe-bench-lite-read-to-edit-repair-admissible-protocol.json`
- `configs/integrations/swe-bench-lite-read-to-edit-repair-admissible-calibration-result.json`
- `configs/integrations/swe-bench-lite-read-to-edit-repair-admissible-result.json`
- `docs/roadmap/swe-bench-lite-experiment-log.md`
- `docs/roadmap/harness-comparison-experiment-design.md`
- `configs/integrations/tool-ux-v2-cross-model-smoke-result.json`
- `configs/integrations/swe-bench-lite-pvlib1854-official-harness-evidence.json`
- `configs/integrations/swe-bench-lite-pi-claw-ablation-sqlfluff1763-result.json`
- `configs/integrations/swe-bench-lite-runtime-environments-v1.json`

## 2. What Git does not transfer

The following are intentionally ignored and must be reconstructed or copied
separately:

- `.env` and API credentials;
- `.port_sessions/` raw Episodes, calibration workspaces, and virtual environments;
- `benchmarks/swe_bench_lite/repos/` task checkouts;
- model weights, Adapter checkpoints, caches, and other large training artifacts.

Do not commit credentials, nested repository `.git` directories, hidden evaluator
content, or raw trajectories merely to simplify migration. If historical raw
Episodes are needed, transfer `.port_sessions` through private storage and verify
the hashes recorded in the versioned evidence summaries.

## 3. Bootstrap the new device

Use Linux or WSL for the historical benchmark environments. From a clean clone:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
export PYTHONPATH=src
python tools/validate_task_suite.py --manifest task_suites/manifest.json
python tools/validate_task_suite.py --manifest task_suites/medium/manifest.json
python -m unittest discover -s tests -v
```

Copy `.env.example` to `.env`, retain only the intended protocol variables, and
configure the Anthropic-compatible DeepSeek endpoint locally. The frozen experiment
expects model identity `deepseek-v4-flash`, temperature 0, thinking disabled, and
4096 maximum response tokens. Never commit `.env`.

Reconstruct the two ignored task repositories at their exact commits:

```bash
mkdir -p benchmarks/swe_bench_lite/repos
git clone https://github.com/pylint-dev/astroid.git \
  benchmarks/swe_bench_lite/repos/pylint-dev__astroid-1268
git -C benchmarks/swe_bench_lite/repos/pylint-dev__astroid-1268 \
  checkout --detach ce5cbce5ba11cdc2f8139ade66feea1e181a7944

git clone https://github.com/pydicom/pydicom.git \
  benchmarks/swe_bench_lite/repos/pydicom__pydicom-1694
git -C benchmarks/swe_bench_lite/repos/pydicom__pydicom-1694 \
  checkout --detach f8cf45b6c121e5a4bf4a43f71aba3bc64af3db9c

python -m unittest tests.test_swe_bench_lite_selection -v
```

Create Python 3.8 environments with `uv`; the lock files reproduce the packages
that passed local admission on the original machine:

```bash
uv venv --python 3.8 .port_sessions/environments/astroid-1268-py38
uv pip install --python .port_sessions/environments/astroid-1268-py38/bin/python \
  -r configs/integrations/astroid-1268-py38-lock.txt

uv venv --python 3.8 .port_sessions/environments/pydicom-1694-py38
uv pip install --python .port_sessions/environments/pydicom-1694-py38/bin/python \
  -r configs/integrations/pydicom-1694-py38-lock.txt
```

These locks reproduce the observed local environments, not official SWE-bench
Docker images. Re-run the admission gate on the new device before any model call:

```bash
PYTHONPATH=src python tools/calibrate_swe_bench_lite.py \
  --benchmark-root benchmarks/swe_bench_lite \
  --instance-id pylint-dev__astroid-1268 \
  --python .port_sessions/environments/astroid-1268-py38/bin/python \
  --output-dir .port_sessions/handoff-calibration-astroid-1268 \
  --timeout 300

PYTHONPATH=src python tools/calibrate_swe_bench_lite.py \
  --benchmark-root benchmarks/swe_bench_lite \
  --instance-id pydicom__pydicom-1694 \
  --python .port_sessions/environments/pydicom-1694-py38/bin/python \
  --output-dir .port_sessions/handoff-calibration-pydicom-1694 \
  --timeout 300
```

Both commands must report `passed`. A failure is an environment/admission event;
record it before remediation and do not spend model calls until the four expected
states are restored: baseline target fails, baseline regressions pass, Oracle target
passes, and Oracle regressions pass.

The calibration CLI loads and validates only the requested `--instance-id` local
workspace while still validating the complete versioned pilot metadata. The other
ignored pilot repositories do not need to be reconstructed for these two commands.

The commands above reproduce the older completed read-to-edit experiment. For the
current seven-task Docker pilot, do not export a task environment to the host.
Load or rebuild the exact images recorded in
`configs/integrations/swe-bench-lite-runtime-environments-v1.json`, reconstruct the
selected task checkout at its recorded commit, then use
`tools/run_swe_bench_lite_runtime_ablation.py --claw-sandbox-backend docker`.
The entrypoint resolves the task image, both interpreters, allowlist, and Pi image
from that manifest and fails before any model request if the workspace import,
pytest, image identity, or multiprocessing semaphore check fails.

## 4. Completed frozen experiment (do not rerun)

Read these files before execution:

1. `docs/roadmap/read-to-edit-constraint-repair-design.md`;
2. `docs/roadmap/read-to-edit-constraint-repair-admissible-ablation-protocol.md`;
3. `configs/integrations/swe-bench-lite-read-to-edit-repair-admissible-protocol.json`;
4. `configs/integrations/swe-bench-lite-read-to-edit-repair-admissible-calibration-result.json`.

The four arms below were completed once on generation commit `3c7f951`. Do not
rerun them as quality retries. Use the versioned result above and the raw ignored
Episodes if a new derived analysis is required.

The immutable order is:

1. Astroid-1268 Control (`repair_attempts=0`);
2. Astroid-1268 Repair (`repair_attempts=1`);
3. pydicom-1694 Repair (`repair_attempts=1`);
4. pydicom-1694 Control (`repair_attempts=0`).

Use one valid Episode per arm, no quality retry. For every command, set
`generation_commit=$(git rev-parse HEAD)` and require a clean task-related worktree.
The common collector arguments are:

```text
--api-config-root .
--model deepseek-v4-flash
--temperature 0
--thinking-mode disabled
--max-tokens 4096
--max-turns 18
--completion-reminder-turns 4
--completion-critical-turns 1
--force-final-response-at-critical
--implementation-deadline-turns 4
--implementation-escalation-turns 2
--force-direct-mutation-after-escalation
--implementation-target-read-allowance 1
--timeout 180
--prompt-version swe-bench-lite-dev.deepseek-v4-flash.v1
```

Add `--implementation-constraint-repair-attempts 0` for Control or `1` for
Repair. Astroid uses `--allow-path astroid/nodes/as_string.py`; pydicom uses
`--allow-path pydicom/dataset.py`. Use distinct, never-reused output roots under
`.port_sessions/` and preserve failed runs.

Do not analyze arm effectiveness until all admitted pairs are archived. Report
three layers separately:

- H1 action recovery: did a rejected post-read request become an allowlisted edit?
- H2 bounded safety: was the invalid request undispatched, correction count at most
  one, and no new task information exposed?
- H3 hard success: did target, regression, Diff, permission, format, termination,
  and evidence gates all pass?

H1 without H3 is protocol-compliance recovery, not coding-quality improvement. If
the repair never triggers, the Episode does not estimate its causal effect. Never
rewrite Trajectory v2; derive diagnostics and add a new versioned result summary.

## 5. Training continuation after the ablation

Do not start LoRA merely because the runtime mechanism is implemented. First:

1. retain the four archived frozen Episodes and their versioned result;
2. use their completed classification without allowing Reviewer scores to override
   hard tests;
3. keep Dev Episodes out of SFT and collect additional family-safe Train Episodes;
4. run leakage, tool-alignment, deduplication, quality, and LlamaFactory export;
5. run `DryRunBackend` and retain `training_verified=false`;
6. only then run the real PEFT backend with a pinned base-model revision and full
   config, followed immediately by the fixed independent Base/Adapter benchmark.

DataFlow/DataFlex and LlamaFactory integration boundaries are documented in
`docs/architecture/data-centric-agent-training-design.md` and
`docs/roadmap/data-centric-training-roadmap.md`. AgentFlow remains a later
orchestration option and must not replace the current evidence-first main line.

## 6. Required handoff record for each continuation

Append to `docs/roadmap/swe-bench-lite-experiment-log.md`:

- purpose and falsifiable hypothesis;
- exact commit, model identity, configuration, task commit, and environment;
- observed trajectory event IDs/hashes and what they directly show;
- inference drawn from those observations and plausible alternatives;
- failure symptom, cause classification, remediation, and rerun result;
- supported and unsupported claims;
- next frozen decision.

Update machine-readable evidence under `configs/integrations/`, then run
`git diff --check` and the full test suite before the next commit.

## 7. Resource-bounded Harness comparison

Read `docs/roadmap/harness-comparison-experiment-design.md` before adding a new
comparison arm. Preserve the three-way separation:

1. pinned DeepSeek Harness `sdk-minimal` as the external reference;
2. Claw Minimal with advanced controls disabled as the parity baseline;
3. Claw Controlled with only preregistered runtime-policy changes.

Use the same DeepSeek-V4-Flash version, effort, prompt, task text, container,
allowlist, token/turn/tool/time budget, and independent verifier. Freeze the DSH
commit and complete profile tree in a versioned protocol before any model call.
Run one fresh Episode per task/arm in the first pass. Repeat paired disagreements
and a preregistered random sample of agreements; never replace the first-run table
with best-of-N results. Report raw Resolved even when policy-compliant Resolved is
the primary metric.

Git transfers source, protocols, small evidence summaries, dependency locks, and
container recipes. It does not transfer DeepSeek Harness checkouts, Docker images,
raw `.port_sessions`, model weights, or credentials. Recreate those assets on the
new host, record their exact commits/digests, and re-run zero-model-call admission
before spending API budget.
