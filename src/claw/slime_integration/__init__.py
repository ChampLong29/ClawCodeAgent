"""SLIME integration for Claw Code Agent.

This package bridges Claw's multi-turn agent environment with SLIME's
RL training framework. Three operating modes are supported:

1. **Normal** (default): Agent uses standard API clients (OpenAI/Anthropic).
   No SLIME dependency. Launched via `claw agent-chat`.

2. **Data Collection**: Agent runs normally but records full trajectories
   to JSONL for offline RL training. Log-probs computed via post-hoc replay.
   Launched via `claw train --mode collect`.

3. **On-Policy**: Agent's model client is replaced with SGLangTrainingClient
   which returns token-level log-probs in real-time. Orchestrated by SLIME's
   rollout framework. Launched via SLIME's `train.py --rollout-function-path
   claw.slime_integration.rollout:generate_rollout`.

Architecture:
    LocalCodingAgent (unchanged)
        └── Model Client (pluggable)
              ├── OpenAICompatClient   (normal mode)
              ├── AnthropicClient      (normal mode)
              ├── TrajectoryRecorder   (data_collection mode, wraps any client)
              └── SGLangTrainingClient (on_policy mode, returns log_probs)
"""
