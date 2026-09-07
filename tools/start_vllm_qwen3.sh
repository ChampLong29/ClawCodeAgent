#!/usr/bin/env bash
set -euo pipefail

CLAW_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAW_MODEL_SNAPSHOT="${CLAW_MODEL_SNAPSHOT:-/home/longwanzhou/.cache/modelscope/models/Qwen--Qwen3-1.7B/snapshots/master}"
CLAW_VLLM_PORT="${CLAW_VLLM_PORT:-8000}"

if [[ ! -x "${CLAW_PROJECT_ROOT}/.venv-vllm/bin/vllm" ]]; then
  echo "Missing .venv-vllm. Create it with:" >&2
  echo "  uv venv --python 3.12 .venv-vllm" >&2
  echo "  uv pip install --python .venv-vllm/bin/python 'vllm==0.28.0' --torch-backend=auto" >&2
  exit 2
fi
if [[ ! -f "${CLAW_MODEL_SNAPSHOT}/config.json" ]]; then
  echo "Model snapshot is invalid: ${CLAW_MODEL_SNAPSHOT}" >&2
  exit 2
fi

# vLLM's V2 runner currently requires UVA, which WSL disables. Its FlashInfer
# sampler also misdetects this RTX 5080 (SM 12.0), so use the supported native
# fallbacks while retaining vLLM scheduling, KV cache, and CUDA graphs.
export VLLM_USE_V2_MODEL_RUNNER=0
export VLLM_USE_FLASHINFER_SAMPLER=0

exec "${CLAW_PROJECT_ROOT}/.venv-vllm/bin/vllm" serve "${CLAW_MODEL_SNAPSHOT}" \
  --served-model-name Qwen/Qwen3-1.7B \
  --host 127.0.0.1 \
  --port "${CLAW_VLLM_PORT}" \
  --api-key local-token \
  --dtype bfloat16 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.85 \
  --generation-config vllm \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking":false}'
