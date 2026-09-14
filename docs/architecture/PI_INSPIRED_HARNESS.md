# Pi-inspired Harness extension design

Status date: 2026-09-12

This change uses Pi as a public reference implementation and comparison
baseline. It does not turn Claw into a Pi fork. The implementation remains in
Python, preserves Claw's permission, trajectory, verification, lifecycle, and
training boundaries, and records Pi-specific provenance explicitly.

## 1. Scope and evidence status

| Capability | Status | Claw implementation |
| --- | --- | --- |
| Ordered lifecycle event bus | Implemented | `src/claw/runtime_events.py` |
| Declarative plugin lifecycle/tool hooks | Implemented | `PluginRuntime.bind_event_bus()` |
| Append-only tree session entries | Implemented | `SessionEntry` and `AgentSession.navigate_to()` |
| Structured compaction records | Implemented | `AgentSession.append_compaction()` |
| Legacy session loading | Implemented | JSON and pre-v2 JSONL are migrated in memory |
| Pi JSONL RPC client | Implemented and contract-tested | `PiRpcClient` |
| Pi-to-Trajectory v2 event projection | Implemented and contract-tested | `PiRpcAgent` |
| Pi under Claw Episode/Verification | Implemented adapter path | `PiRpcBenchmarkAdapter` |
| Versioned Pi benchmark CLI | Implemented and contract-tested | `claw benchmark-pi` |
| Guarded Claw–Pi comparison report | Implemented and contract-tested | `claw benchmark-compare` |
| One real Claw–Pi local Dev comparison | Benchmark verified, narrow scope | Archived Marshmallow-1343 evidence with pinned controls |
| Three-arm Claw Base / Enhanced / Pi pilot | Planned | Versioned seven-task plan is ready; paid Episodes have not been run |
| Official SWE-bench Claw–Pi result | Planned | Must execute archived candidates through the official isolated Harness |

“Contract-tested” means local tests exercised the protocol adapter with a fake
JSONL process. Separately, one real controlled local SWE-bench Lite Dev comparison
has been archived. That one sample is not an official score and does not establish
statistical runtime superiority.

## 2. Why these mechanisms were selected

Pi's useful lesson is not a single tool implementation. It is the separation of
a small agent loop from extensions, an append-only navigable session, and a
headless RPC interface. Claw already has stronger structured workflows and an
auditable training/benchmark chain, so the selected design adds those mechanisms
without replacing Claw's existing strengths.

```text
Claw CLI / GUI / Benchmark
          |
          v
 LocalCodingAgent -------- RuntimeEventBus -------- declarative plugin hooks
          |                         |
          |                         +-- mutate approved payload fields
          |                         +-- cancel lifecycle/tool operations
          v
 Tool policy -> permission -> execution -> Trajectory v2
          |
          v
 AgentSession active branch
          |
          +-- append-only SessionEntry tree
          +-- compaction metadata node (history is not rewritten)

PiRpcBenchmarkAdapter
          |
          +-- isolated Claw Episode workspace
          +-- `pi --mode rpc --no-session`
          +-- Pi events -> Claw Trajectory v2
          +-- Claw VerifierPipeline -> comparable report
```

## 3. Runtime event contract

`RuntimeEventBus` is synchronous and deterministic. Lower numeric priority runs
first; equal priority retains registration order. Exact-event and wildcard
listeners share the same ordering. A listener may:

- observe the event;
- return static `payload_updates`;
- cancel the current operation with a reason;
- fail without crashing the Agent by default, or opt into `fail_closed`.

The Agent emits these implemented events:

- `before_agent_start`, `agent_start`, `agent_end`, `agent_settled`;
- `turn_start`, `turn_end`;
- `before_model_request`, `model_response`;
- `before_tool_call`, `tool_result`;
- `before_compact`, `after_compact`.

Blocked tools and aliases are still resolved before `before_tool_call`.
Permissions are still checked by the normal tool executor. An event hook cannot
declare a permission grant or bypass the policy path. Cancellation and hook
errors are projected as `runtime_guidance` facts when a trajectory observer is
active.

Workspace plugin JSON remains data, not executable code. For example:

```json
{
  "name": "project-guard",
  "hooks": {
    "before_agent_start": {
      "append_system_prompt": "Follow the repository acceptance checklist."
    }
  },
  "tool_hooks": {
    "bash": {
      "before": {
        "deny": true,
        "argument_equals": {"command": "unsafe"},
        "reason": "Command blocked by project guard"
      }
    }
  }
}
```

Arbitrary workspace Python/JavaScript hook loading is intentionally not part of
this phase because it would create a new code-execution trust boundary.

## 4. Tree session and compaction contract

Each v2 session entry contains:

```text
entry_id -> globally unique node id
parent_id -> previous node on this branch, or null
entry_type -> message | compaction
payload -> message or structured compaction data
created_at -> append timestamp
label -> optional user-facing marker
```

`messages` remains a compatibility view of the active branch. Calling
`navigate_to(entry_id)` rebuilds this view. The next message uses that node as
its parent, leaving later entries from the old branch intact.

Compaction affects only the model-facing message view. The session appends a
`compaction` entry containing the summary, `first_kept_entry_id`, estimated
`tokens_before`, and extracted file/command/read/conclusion details. Historical
message entries are not edited. This keeps the session useful for audit and
later branch navigation.

The session store writes v2 entry records incrementally and appends metadata
snapshots so mutable fields remain current. It still loads legacy single-JSON
sessions and old raw-message JSONL sessions.

## 5. Pi RPC baseline contract

`PiRpcClient` starts Pi with RPC mode and no Pi-side persistence by default:

```text
pi --mode rpc --no-session [--provider PROVIDER] [--model MODEL]
```

It sends a correlated `prompt` command, consumes strict JSONL records until
`agent_settled`, then asks for `get_session_stats`. `agent_end` is not treated as
final because Pi documents that retry, compaction, or queued continuations may
still follow it.

`PiRpcAgent` maps each Pi event exactly once:

| Pi event | Claw trajectory event |
| --- | --- |
| `turn_start` | `model_request` |
| assistant `message_end` | `model_response` |
| `tool_execution_start` | `tool_call` |
| `tool_execution_end` | `tool_result` |
| compaction/retry/extension error | `runtime_guidance` |

`PiRpcBenchmarkAdapter` requires a non-empty `sandbox_attestation`, then reuses
Claw's existing isolated Episode,
allowlisted Diff, test collection, Verification v2, metrics, and archival path.
This makes the baseline comparable without claiming that Pi and Claw have the
same internal architecture.

The attestation is an operator-supplied evidence label recorded in the
trajectory. The adapter can enforce the repository's macOS Seatbelt profile
for the complete Pi process. On Docker hosts, `PiDockerRpcClient` constructs a
digest-pinned, no-implicit-pull container for the complete JSONL process with a
non-root user, read-only RootFS, dropped capabilities and resource limits. It
also performs exact-name cleanup after the attached Docker process stops. This
client is not owned by Claw's session `SandboxBackend`, so the operator's
attestation remains distinct from the Agent/Verifier Sandbox evidence.

`claw benchmark-pi` exposes this path without bypassing those requirements. It
requires explicit Pi runtime/tool versions, model reference, and sandbox
attestation. `--use-claw-api-config` writes a Pi provider file containing only
an environment-variable reference and passes the secret in process memory;
`--enforce-macos-seatbelt` enables the in-process launcher boundary.
Both per-response `--max-tokens` and cumulative `--max-total-tokens` are frozen
in benchmark controls; the latter defaults to 250,000 and aborts only after a
completed tool-bearing turn, because Pi RPC does not expose mid-response token
preemption.
`claw benchmark-compare` accepts archived Claw and Pi
`benchmark-run.json` files, verifies the ordered task set and shared controls,
and writes `runtime-comparison.json` plus `report.md`. A mismatch remains visible
as `comparability.comparable = false`; the command does not silently normalize
different protocols.

Task manifests use a leading bare `python` as a portable runtime marker. Episode
checks resolve that token to the interpreter running Claw and retain both the
manifest command and `resolved_command` in verification evidence. This prevents
GUI or packaged environments without a `python` executable on `PATH` from
turning correct implementations into false test failures.

Pi itself states that its default process does not provide a built-in permission
sandbox. Therefore a real baseline run must use the same container or sandbox
boundary as the Claw run; running both directly with user permissions is not a
fair safety comparison.

## 6. Fair comparison protocol

A report may call the result “Benchmark verified” only after all of the following
are fixed and archived:

1. the same immutable Test manifest and workspace template;
2. the same model ID/revision, provider, temperature/thinking settings, and
   maximum tool-bearing turns where supported;
3. explicitly recorded filesystem/network containment; any difference must
   remain a treatment difference rather than an equivalence claim;
4. the same hidden tests and Diff allowlist;
5. separate append-only trajectories and Verification v2 reports;
6. the pinned Pi version and Claw commit/runtime version.

Pi RPC does not expose a temperature-setting command. With
`anthropic-messages`, the requested temperature is therefore recorded but not
explicitly enforced by Pi; the strict report records
`temperature_is_explicit = false` and refuses to call that run fully
comparable. OpenAI-compatible custom providers can enforce it through model
sampling parameters.

Recommended ablations are Claw base, Claw plus lifecycle events, Claw plus tree
session/structured compaction, and Pi RPC baseline. Do not describe a fake-RPC
contract test as a Pi benchmark and do not describe local SWE-bench calibration
as an official SWE-bench score.

The checked-in three-arm plan intentionally uses the seven currently calibrated
tasks as an ordered subset of the wider 20-task local selection. Expanding the
plan requires adding environment-calibration evidence first; the task list must
not be widened merely to satisfy a manifest-count assertion.

## 7. Current evidence and next Docker-host step

The archived one-task comparison is
`configs/integrations/swe-bench-lite-claw-pi-deepseek-flash-20260911.json`.
It records a successful allowlisted Claw edit and a Pi run that localized the
issue but made no source mutation. The immutable local artifacts remain outside
Git under `.port_sessions/`; the checked-in JSON stores their hashes and narrow
claim boundary.

The current preregistered pilot is
`configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v2.json`. It preserves
the v1 task order and `pi@0.85.1` controls while naming the reconstructable
`@earendil-works/pi-coding-agent@0.85.1` package and Docker boundary. On a Docker
host, continue in this order:

1. validate Claw's Docker Backend with
   `docs/architecture/DOCKER_SANDBOX_VALIDATION_RUNBOOK.md`;
2. pin a Pi image/runtime and use `--pi-docker-image` to keep the JSONL process
   inside that container for its entire lifetime;
3. run one model-free RPC smoke and one calibrated task before the seven-task
   paid pilot;
4. archive both local Verification v2 and official Harness evidence before
   making an official Benchmark claim.

The three-arm entrypoint now accepts a digest-pinned Claw image plus distinct
in-container task and optional evaluator Python executables. Claw Base, Claw Enhanced, and every arm's
Verifier use that backend; Docker task commands stage the evaluator with hidden
assets rather than referencing host-only paths. The Marshmallow-1343 image has
passed local baseline/reference Docker calibration, and the fixed Pi image has
answered a network-disabled empty-session RPC stats request with zero tokens.
No paid model call was made. Pi must use Provider network access for real runs;
because its tools share that process, this differs from Claw's offline Shell and
prevents a network-policy parity claim.

## 8. Upstream references and provenance

The design was informed by the public MIT-licensed Pi repository and these
official sources, read on 2026-09-09:

- [Pi repository and package boundaries](https://github.com/earendil-works/pi)
- [Pi coding-agent package](https://github.com/earendil-works/pi/tree/main/packages/coding-agent)
- [Pi agent loop](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts)
- [Pi extension runner](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/extensions/runner.ts)
- [Pi session manager](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)
- [Pi compaction contract](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)
- [Pi RPC contract](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)
- [Pi license](https://github.com/earendil-works/pi/blob/main/LICENSE)

The Claw code in this change is an independent Python implementation of these
general architectural ideas. No Pi source file was copied into the repository.
