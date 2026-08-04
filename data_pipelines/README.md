# Data-centric Agent Pipelines

`agent_sft_v1.py` is the DataFlow-native wrapper for Claw's deterministic M2
governance operators. The policy implementation remains under
`src/claw/data_pipeline/`, while this directory contains the optional DataFlow
execution layer.

## Environment boundary

Use Python 3.11 and a dedicated environment. Do not install DataFlow into the
Claw runtime or the CUDA/DataFlex training environment.

```powershell
py -3.11 -m venv .venv-dataflow
.\.venv-dataflow\Scripts\python.exe -m pip install -U pip
.\.venv-dataflow\Scripts\python.exe -m pip install -e external/dataflow
.\.venv-dataflow\Scripts\python.exe -m pip install -e . --no-deps
```

Run the pipeline against a previously materialized Silver dataset:

```powershell
.\.venv-dataflow\Scripts\python.exe -m data_pipelines.agent_sft_v1 `
  --silver-records artifacts/silver/silver-records.jsonl `
  --silver-manifest artifacts/silver/silver-manifest.json `
  --cache-path artifacts/dataflow/agent_sft_v1
```

The DataFlow cache contains the output of every versioned operator. For the
complete Gold Manifest, exclusion report, Data Card and LLaMAFactory export,
run the dependency-free `AgentSFTGovernancePipeline` from the Claw environment.

The pinned upstream commits and compatibility boundary are recorded in
`configs/integrations/data-centric-upstreams.json`.
