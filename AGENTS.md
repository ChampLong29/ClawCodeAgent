# AGENTS.md

This file is the contributor guide for coding agents working in this repository. Keep it aligned with the implementation; do not use file counts or test counts that become stale after normal development.

## Project Overview

Claw Code Agent is a Python implementation of a local coding-agent runtime. Its main capabilities are:

- Anthropic-native and OpenAI-compatible model clients.
- Multi-turn tool calling with local filesystem and shell tools.
- Session persistence, context injection, budgets, and compaction.
- Extensible runtime modules, MCP tools, plugins, policies, and skills.
- DevFlow and Lifecycle structured software-development workflows.
- Lightweight rollout generation and an auditable V2 training/benchmark pipeline.

The package lives under `src/claw/`; imports use the `claw` namespace.

## Development Setup

Python 3.9 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate       # Linux / macOS
# .venv\Scripts\Activate.ps1   # Windows PowerShell

python -m pip install -U pip
python -m pip install -e ".[dev]"
```

The installed command entry points are:

- `claw`
- `claw-agent`
- `claw-tui`

`train-web` is a `claw` subcommand, not a separate `claw-train-web` executable.

If the package is not installed, run from the repository with `PYTHONPATH=src python -m claw.main ...`. On PowerShell, set `$env:PYTHONPATH = "src"` first.

## Model Configuration

Copy `.env.example` to `.env` and choose one protocol.

DeepSeek through the Anthropic-compatible protocol (project default):

```dotenv
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_API_KEY=sk-your-deepseek-key
ANTHROPIC_MODEL=deepseek-flash
```

OpenAI compatible:

```dotenv
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=sk-your-deepseek-key
OPENAI_MODEL=deepseek-flash
```

Any `ANTHROPIC_*` variable selects Anthropic mode. Use only `OPENAI_*` variables for OpenAI-compatible mode. The shared fallback model is `deepseek-flash`, currently backed by `v4-flash-9_10`. Never commit `.env` or credentials.

For repeated local Qwen3 Episodes, use the pinned, persistent vLLM service in
`tools/start_vllm_qwen3.sh`. The in-process Transformers client is a protocol
smoke/reference path only; do not use it to report benchmark throughput or
SWE-bench success rates. Record server startup/JIT time separately from warm
request latency.

## Common Commands

Run the Agent:

```bash
claw agent "task" --cwd . --stream
claw agent "task" --cwd . --max-turns 50 --stream
claw agent "task" --cwd . --sandbox-backend docker --sandbox-image python:3.12-slim --stream
claw agent-chat --cwd . --max-turns 30
claw tui --cwd .
```

Run tests:

```bash
python -m unittest discover -s tests -v
python -m unittest tests.test_agent_runtime -v
```

Validate versioned task suites:

```bash
python tools/validate_task_suite.py --manifest task_suites/manifest.json
python tools/validate_task_suite.py --manifest task_suites/medium/manifest.json
```

Run a one-task benchmark:

```bash
claw benchmark-run \
  --manifest task_suites/medium/manifest.json \
  --group base \
  --limit 1 \
  --output .port_sessions/benchmark-medium
```

Run a pinned Pi RPC baseline and compare archived Claw/Pi results:

```bash
claw benchmark-pi --help
claw benchmark-compare --help
```

Run one controlled local SWE-bench Lite Dev comparison or three-arm ablation:

```bash
python tools/compare_swe_bench_lite_runtimes.py --help
python tools/run_swe_bench_lite_runtime_ablation.py --help
```

Pi RPC requires an explicit isolation attestation. `--enforce-macos-seatbelt`
enforces the bundled macOS profile; on Docker hosts, run Pi inside an
operator-managed container boundary and record that boundary. The Pi RPC process
is not yet owned by Claw's session `SandboxBackend`.

The three-arm entrypoint can construct the complete Pi container through
`--pi-docker-image`. Require a digest pin, no implicit pull, non-root execution,
a read-only root filesystem, dropped capabilities, and resource limits. Pi
provider calls and tools share that container's network; record the difference
from Claw's offline Shell sandbox and do not claim network-policy parity.

For the three-arm SWE-bench Lite Dev entrypoint, Docker Claw arms require
`--claw-sandbox-backend docker`, a digest-pinned `--claw-sandbox-image`, and
`--claw-sandbox-python` naming the historical task interpreter inside that
image. Use `--claw-sandbox-evaluator-python` when the staged Claw evaluator
requires a newer interpreter than the task. All three Verifier runs use that Backend. Pi itself still runs through the explicitly
attested host executable supplied as `--pi-executable`, or through the explicit
`--pi-docker-image` boundary; disabling Seatbelt requires
`--pi-sandbox-attestation`.

Use `--api-config-root <dir>` when benchmark arms need isolated model-provider
configuration. It changes API configuration discovery only, not the task workspace.

Docker benchmarks require a local digest-pinned image and use distinct Agent
and Verifier sandboxes:

```bash
claw benchmark-run \
  --manifest task_suites/manifest.json \
  --sandbox-backend docker \
  --sandbox-image 'python@sha256:<64-hex-digest>' \
  --limit 1 \
  --output .port_sessions/benchmark-docker
```

Before committing:

```bash
git diff --check
python -m unittest discover -s tests -v
```

## Architecture

### Agent request path

```text
CLI / REPL / TUI / GUI / Bridge
  -> claw.main / frontend route
  -> QueryEngine or LocalCodingAgent
  -> PromptContext + SystemPromptBuilder
  -> Anthropic/OpenAI-compatible ModelClient
  -> ToolRegistry / Executor
  -> AgentSession + SessionStore
  -> Runtime modules
```

Important modules:

- `src/claw/main.py` — CLI parser and command routing.
- `src/claw/agent_runtime.py` — main model/tool loop, retries, budgets, permissions, and session save.
- `src/claw/agent_tools.py` — built-in tool schemas, handlers, registry, and execution results.
- `src/claw/container_runtime.py` — cross-platform Docker/Podman execution boundary for Agent shell commands and Episode checks.
- `src/claw/sandbox_backend.py` — backend-neutral execution contracts and the non-isolating Host compatibility backend.
- `src/claw/docker_backend.py` — explicit, fail-closed Docker container backend and mount admission.
- `docs/architecture/DOCKER_SANDBOX_VALIDATION_RUNBOOK.md` — Docker-host
  live validation, benchmark pilot, evidence checks, and remaining limitations.
- `src/claw/openai_compat.py` — Anthropic and OpenAI-compatible clients, including streaming.
- `src/claw/agent_context.py` — Git, environment, `AGENTS.md`, and runtime context collection.
- `src/claw/agent_prompting.py` — system-prompt assembly.
- `src/claw/agent_session.py` and `src/claw/session_store.py` — messages and persisted sessions.
- `src/claw/runtime_events.py` — ordered lifecycle events and cancellation/mutation directives.
- `src/claw/token_budget.py`, `src/claw/compact.py`, `src/claw/microcompact.py` — budget and context control.
- `src/claw/bash_security.py` — shell policy validation.

### Structured development path

- `src/claw/devflow_runtime.py` — architecture, step definition, module analysis, implementation, and verification.
- `src/claw/lifecycle_runtime.py` — requirements through acceptance, delegating development phases to DevFlow.
- `src/claw/questionnaire_runtime.py` — runtime-paced requirements questions.
- `src/claw/deep_dive_runtime.py` — isolated technical research sessions.
- `src/claw/context_manager.py` — phase-level compaction while preserving structured outputs.

### Auditable training and benchmark path

```text
TaskSuiteManifest
  -> EpisodeOrchestrator
  -> RuntimeAdapter + LocalCodingAgent
  -> Trajectory v2
  -> Verification v2
  -> DatasetBuilder
  -> TrainingBackend
  -> BenchmarkRunner
  -> ExperimentRegistry
```

Important packages:

- `src/claw/task_suite/` — versioned task manifests, hashes, split rules, and validator.
- `src/claw/episode/` — isolated workspaces, checkpoints, hidden-test staging, and recovery.
- `src/claw/trajectory/` — append-only trajectory and tool-call evidence.
- `src/claw/verification/` — independent automated and reviewer signals.
- `src/claw/dataset/` — selection, deduplication, leakage checks, conversion, and manifests.
- `src/claw/training_backends/` — dry-run contract validation and PEFT LoRA/QLoRA SFT.
- `src/claw/benchmark/` — independent execution, metrics, cost, and ablation reports.
- `src/claw/benchmark/pi_rpc_adapter.py`, `pi_command.py`, and
  `runtime_comparison.py` — attested Pi RPC execution and guarded Claw–Pi reports.
- `src/claw/experiment/` — stateful experiment registry and content-addressed artifacts.
- `src/claw/training/` — legacy/lightweight `CodingTask` rollout path used by `claw train`.

Do not conflate the lightweight rollout JSONL with the V2 experiment record. The former is useful for exploration; the latter provides task/version/data/training/benchmark lineage.

Trajectory v2 remains the immutable event source. Derive path-localization and edit-timing
signals with `claw.trajectory.analyze_rollout_behavior()` or
`tools/analyze_rollout_behavior.py`; do not rewrite historical trajectory events to add
diagnostics. `direct_mutation` means an observed explicit file-edit tool, not proof that Shell
commands had no side effects. Behavior diagnostics v4 distinguishes model-requested,
dispatched, and policy-rejected tool calls; a rejected request can still provide
path-localization evidence without being counted as an executed inspection or mutation.
Sandbox cleanup is recorded as a `sandbox_lifecycle` event; do not infer successful
cleanup merely from the absence of later tool calls.

## Repository Layout

```text
.
├── src/claw/                 Python package
│   ├── gui/                  HTTP routes, SSE, permission handling, chat UI
│   ├── training/             Lightweight rollout subsystem
│   ├── task_suite/           Versioned task definitions
│   ├── episode/              Episode lifecycle and recovery
│   ├── trajectory/           Append-only execution evidence
│   ├── verification/         Independent verification
│   ├── dataset/              Dataset production
│   ├── training_backends/    Dry-run and PEFT SFT
│   ├── benchmark/            Benchmark runner and reports
│   └── experiment/           Experiment registry
├── tests/                    Unit and integration tests
├── task_suites/              Core smoke and medium pilot suites
├── benchmarks/               External benchmark snapshots and metadata
├── tools/                    Deterministic generators and validators
├── examples/training/        Lightweight rollout example
├── .port_sessions/           Local runtime and experiment output; not source
├── projects/                 Generated project workspaces
├── README.md                 User-facing overview
└── TRAINING_GUIDE.md         Training and evaluation guide
```

`benchmarks/swe_bench_lite/repos/` contains ignored local working copies. Commit the versioned raw snapshot, selection metadata, and repository snapshot metadata only. Do not accidentally add nested repository `.git` data.

`tools/probe_container_runtime.py` performs a model-free real-engine smoke test
for the OCI boundary. Its output is runtime evidence, not a task Benchmark.

The official SWE-bench evaluator is a separate boundary implemented by
`src/claw/benchmark/swe_bench_official.py`. Keep it in `.venv-swebench`, pin the
package through `configs/benchmarks/swebench-harness.txt`, require both CLI and
Python Docker SDK preflight, and never expose its frozen raw row to the Agent.
Prepared inputs are not an official result; archive the Harness report before
claiming Benchmark verification.

## Runtime and Extension Rules

### Tool registry

`default_tool_registry()` returns the global registry. Built-in tools are registered in code; MCP and plugin virtual tools are registered dynamically.

Plugin virtual tools have two modes:

- `command` present: execute a command template with validated arguments.
- no `command`: return prompt context to the model.

When changing tool execution:

- Route Agent shell and executable plugin commands through the session-owned
  Sandbox Backend. Host mode is compatibility execution, not OS isolation.
- Docker mode requires an explicit local image, must not pull implicitly, and
  must fail closed rather than fall back to Host.
- Session resume inherits its persisted sandbox backend unless the caller
  explicitly selects a replacement boundary.
- Preserve stdout, stderr, return code, timeout, and error detail in `ToolResult`.
- Keep model-visible schemas and handler arguments synchronized.
- Count one model-requested tool call exactly once in trajectories and metrics.
- Preserve provider finish reasons. A token-limited response without a tool call is a stopped run, and its `runtime_stop` fact must remain valid in Trajectory v2.
- Apply blocked tools and aliases before dispatch.
- Keep permission checks intact.
- Tool schema v2 keeps code retrieval bounded and model-usable: `code_outline`
  accepts a literal symbol query, and `grep_search` returns deterministic relative
  paths with bounded pagination, optional file filtering, and limited context.
  Preserve pagination metadata before match payloads so result truncation cannot
  hide whether another page exists. SWE collection exposes only its explicit local
  filesystem, edit, search, and shell allowlist.
- Keep optional implementation-deadline, escalation, and post-edit contract
  guidance traceable as `runtime_guidance`; guidance is not a tool-policy bypass
  or proof of model improvement.
- Keep the optional repeated-read-only guard fail-closed and traceable: reject an
  exact normalized observation only after the same deterministic read-only action
  succeeded with no intervening dispatched `write_file`, `edit_file`, or `bash`.
  At most one no-new-information repair may be configured; a second duplicate must
  stop explicitly. A blocked loop is not evidence of task-quality improvement.
- Keep optional forced-action requests visible in `model_request`: direct-mutation
  constraints may expose only explicit edit tools, or one allowlisted target-file
  read followed by an edit-only request; final-response constraints expose no tools.
  Reject off-target and repeated reads before dispatch. The optional one-attempt
  read-to-edit repair may issue one no-new-information corrective request before an
  explicit stop; record the rejected request and correction as immutable evidence.
  A provider that exhausts the configured repair must stop explicitly; a configured
  but untriggered constraint or repair is not evidence of causal improvement.

### Configuration discovery

Discovery behavior differs by runtime. Do not normalize it without checking tests:

- `hook_policy.py` and `remote_runtime.py` use walk-up discovery.
- `remote_trigger_runtime.py` and `config_runtime.py` use cwd/additional-directory discovery without walk-up.
- `team_runtime.py` also checks `.Codex/teams.json`.
- MCP and plugin runtimes have their own documented candidate names.

### Skills

Bundled skills are currently code-defined in `src/claw/bundled_skills.py`. The `use_skill` tool formats a skill prompt and returns it to the Agent. There is no general filesystem-discovered user-skill registry in the Claw runtime; do not document one unless implemented.

## Session and Context Invariants

- `LocalCodingAgent.run()` resets the Turn counter for each user query.
- Session files are stored under `.port_sessions/agent/<session-id>.jsonl`.
- Session v2 entries form an append-only tree. Navigation changes the active
  branch view without deleting abandoned descendants.
- The saved session includes cwd, model, messages, timestamps, and stop reason.
- Tool results are micro-compacted before they grow the conversation indefinitely.
- Automatic compaction preserves high-priority system/user/tool-call structure
  and appends structured compaction metadata without rewriting historical entries.
- This is resumable session memory, not vector/embedding long-term memory.

When changing session schemas, maintain backward-compatible loading or provide an explicit migration.

## Task Suite Invariants

- `schema_version`, task version, template hash, optional hidden-test hash, and suite content hash are validated.
- Template and hidden-test-asset hashes normalize the executable bit
  (`workspace_hash(..., normalize_exec=True)`) so a manifest validates identically on
  Windows-native, WSL/drvfs, and Linux checkouts; Episode checkpoint integrity keeps
  the mode-sensitive default.
- Core suites require family-safe Train/Dev/Test isolation.
- Small curated suites may declare a stricter local `validation` policy, but defaults must remain suitable for the core suite.
- Initial checks must fail before the solution and final checks must pass after the Oracle is applied.
- Hidden test assets are available only during verification and must be removed before Agent execution.
- Episode task commands run through a short-lived Verifier Sandbox Backend.
  Agent and Verifier owners/handles must remain distinct, and Docker benchmark
  images must be digest-pinned.
- Docker SWE-bench Dev materialization stages the evaluator entrypoint with the
  hidden assets and names task and optional evaluator interpreters inside the
  pinned image. Do not emit host-only absolute evaluator or virtualenv paths
  into Docker task commands, and do not conflate Claw's Python floor with a
  historical task's Python version.
- Destroy the Agent sandbox before final verification. A cleanup failure is an
  infrastructure failure and must not be reported as an ordinary test failure.
- Benchmark Diff allowlists default to paths represented by the task Oracle; explicit `--allow-path` replaces that default.
- SWE collection uses the same allowlist as its implementation-progress boundary: scratch-file edits must not suppress implementation guidance or trigger post-edit contract guidance.
- SWE collection also passes that allowlist into `allowed_write_paths`; explicit
  `write_file` and `edit_file` calls outside it must be rejected before filesystem
  mutation. While an allowlist is active, Shell fails closed unless its command runner
  advertises disposable-workspace execution. The OCI runner copies the current Episode
  workspace, mounts only that copy, and discards all Shell-side changes.
- SWE candidate verification must rebuild from the Episode Git `HEAD`, overlay only
  allowlisted candidate files, and then inject hidden tests. Never copy a possibly
  contaminated Agent test tree into the verifier workspace.
- Generators must be deterministic.

## Training and Evidence Invariants

- `DryRunBackend` verifies contracts only and must report `training_verified = false`.
- Real PEFT training must pin the base-model revision and full training config.
- Dataset construction must reject Split/Family leakage and broken tool-call alignment.
- Reviewer scores remain separate from automated test outcomes.
- The completed Astroid-1268/pydicom-1694 read-to-edit Control/Repair comparison
  has 2/4 hard successes, identical within-task outcomes, and no Repair trigger;
  keep the repair disabled and do not claim causal improvement or quality-retry
  those Dev Episodes.
- Supplemental clean verification of the frozen pvlib-1154 Control and Treatment
  candidates passes 1 FAIL_TO_PASS and 97 PASS_TO_PASS tests for both arms. Preserve
  the original Diff Scope failures and Verification records: this proves the prior
  zero test scores came from evaluator preparation conflict, not that the original
  Episodes were policy-compliant successes.
- The preregistered fresh pvlib-1854 local Dev Episode is a hard success: one
  FAIL_TO_PASS and 281 PASS_TO_PASS tests pass with only `pvlib/pvsystem.py`
  changed. It is one local Dev sample, not an official SWE-bench score or a
  success-rate estimate. Its two Shell calls exposed a Docker Desktop WSL `/tmp`
  bind failure. After enabling distro integration, the same-volume disposable-copy
  fix read the archived candidate through the real Docker engine and discarded a
  container-created marker; it was not followed by a model quality retry.
- Verifier policy v3 keeps `final_response_quality` as a zero-weight Soft signal and
  adds a required zero-weight `evaluation_integrity` Hard gate. Evaluation preparation
  failures leave `test_pass_rate` unknown instead of converting them to a zero score.
  Policy v2 introduced `final_response_quality`
  derived from immutable termination detail. It detects only obvious delivery
  defects and does not override hard test, Diff, permission, format, or termination
  results.
- Non-base benchmark groups require matching Dataset, Training Run, and Experiment references.
- Completed Registry evidence is immutable; do not overwrite it to make a run appear consistent.
- A screened SWE-bench item is not an official result until run through the official isolated Harness.
- Harness comparisons use three distinct arms: a pinned external minimal profile,
  Claw Minimal for protocol/tool parity, and Claw Controlled for preregistered
  policy changes. Keep raw Resolved visible alongside policy-compliant and
  budgeted resolution; do not select metrics after viewing outcomes.
- Under a limited budget, run one fresh Episode per task/arm first. Repeat paired
  disagreements and a preregistered sample of agreements for stability; never
  replace first-run outcomes with best-of-N quality retries.
- For cross-device continuation, follow `TRAINING_HANDOFF.md`. Ignored task repositories,
  `.port_sessions`, credentials, environments, and checkpoints are not transferred by Git;
  reconstruct and revalidate them before model calls or training.

## Coding and Git Rules

- Search with `rg` or `rg --files` first.
- Use `apply_patch` for hand-written edits.
- Preserve unrelated user changes and untracked files.
- Never use destructive Git commands to clean a dirty worktree.
- Stage explicit task-related paths and inspect `git diff --cached` before committing.
- Keep generated task assets and their manifests in the same commit.
- Update `README.md`, `TRAINING_GUIDE.md`, and this file when public commands, architecture boundaries, or evidence claims change.
- Prefer stable descriptions over exact file/test counts.

## Documentation Truthfulness

Use these evidence labels consistently:

- **Implemented** — code exists and focused tests pass.
- **Contract verified** — schema/protocol/dry-run evidence passes; no real training is implied.
- **Training verified** — a real backend produced a validated checkpoint and artifacts.
- **Benchmark verified** — a fixed independent test manifest was executed and the report is reproducible.
- **Planned** — design exists but the end-to-end path is not implemented.

Never describe a dry run as real training, a smoke suite as a capability benchmark, a Reviewer opinion as an automated pass, or a SWE-bench selection snapshot as an official score.
