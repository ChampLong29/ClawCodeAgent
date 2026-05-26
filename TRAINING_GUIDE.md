# Claw Code Agent 智能体能力训练指南

使用本项目的 Lifecycle/DevFlow 运行时 + SLIME 框架训练模型的软件工程智能体能力。

---

## 目录

1. [训练哲学](#训练哲学)
2. [环境与模型配置](#环境与模型配置)
3. [任务套件定义](#任务套件定义)
4. [Rollout 执行](#rollout-执行)
5. [SLIME 集成：何时接入、怎么接入](#slime-集成何时接入怎么接入)
6. [On-Policy vs Off-Policy](#on-policy-vs-off-policy)
7. [评测分离：Reviewer Agent](#评测分离reviewer-agent)
8. [数据飞轮：三阶段迭代](#数据飞轮三阶段迭代)
9. [完整示例：端到端训练流程](#完整示例端到端训练流程)
10. [配置参考](#配置参考)

---

## 训练哲学

### 核心思路

不是训练模型"写代码"，而是训练模型**按软件工程流程完成开发任务**。

Lifecycle 的 10 个阶段（需求分析 → 系统设计 → 架构 → 实现 → 测试 → 验收）本身就是跨领域不变的约束框架。Agent 在每个阶段必须完成特定产出物才能进入下一阶段——这个流程结构是被运行时**强制执行**的，不是靠 prompt 建议的。

训练后的模型学到的是：
- 先理解需求，再做设计，最后才写代码
- 代码写完后主动审查、写测试、做验收
- 遇到不确定的需求时逐题澄清，而不是一次抛出一堆问题
- 选择技术栈时能分析优劣，而不是随便选

### 泛化性来源

```
泛化性 = 共用框架 × 多样变量

共用框架: Lifecycle 10 阶段流程（不变）
多样变量: DomainConfig（领域特定的技术栈、测试框架、审查标准）
```

5 个内置领域覆盖了绝大多数软件开发场景：Web 后端、Web 前端、CLI 工具、数据流水线、SDK 库。不同领域的任务共享相同的流程框架，差异只在于阶段 prompt 中注入的技术约束。

---

## 环境与模型配置

### API 配置

训练时通常用两个不同的模型实例：

| 角色 | 说明 | 模型建议 |
|------|------|---------|
| **Rollout 模型（被训练）** | 执行 lifecycle 任务，生成 trajectory | 待训练的基础模型 |
| **Reviewer 模型（评测）** | 独立审查 rollout 产出物 | 更强的模型（如 Claude），或同一模型不同 session |

```bash
# .env — 两个模型可配不同的 API endpoint

# Rollout 模型（被训练的模型）
OPENAI_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_API_KEY=local-token
OPENAI_MODEL=qwen3-coder-30b

# Reviewer 模型（可选，未设置时复用 rollout 模型的独立 session）
# ANTHROPIC_BASE_URL=https://api.anthropic.com
# ANTHROPIC_API_KEY=sk-ant-your-reviewer-key
# ANTHROPIC_MODEL=claude-sonnet-4-6
```

### 确定性配置（Reproducibility）

训练 rollout 必须可复现，通过 `DeterministicConfig` 控制：

```bash
# CLI 参数
claw train \
  --suite tasks.json \
  --temperature 0.0 \       # 贪婪解码（确定性）
  --seed 42 \               # 固定随机种子
  --disabled-runtimes search,mcp  # 禁用非确定性运行时
```

对应的代码配置：

```python
from claw.training.determinism import DeterministicConfig

det = DeterministicConfig(
    temperature=0.0,              # 贪婪解码
    seed=42,                      # 注入 random.seed()
    session_id="train/episode_0", # 固定 session ID
    disabled_runtimes=["search", "mcp"],  # 禁用非确定性运行时
)
```

**为什么禁用 search/mcp**：这些运行时从外部系统注入上下文，每次运行内容不同，破坏复现性。Git 状态、CLAUDE.md 等本地上下文在 sandbox 中本来就不存在，不需要额外禁用。

### Sandbox 配置

```python
from claw.training.sandbox import SandboxManager

sandbox = SandboxManager(
    default_timeout=120.0,        # 测试执行超时（秒）
    default_max_memory_mb=512,    # 内存限制（当前为软限制）
)
```

---

## 任务套件定义

### CodingTask JSON Schema

```json
{
  "tasks": [
    {
      "id": "web_backend_easy_001",
      "prompt": "为 FastAPI 应用添加用户注册端点：POST /register，接收 username/email/password，返回 JWT token。密码需 bcrypt 哈希。",
      "type": "add_feature",
      "difficulty": "easy",
      "domain": "web-backend",
      "ground_truth_files": {
        "src/routes/auth.py": "from fastapi import APIRouter, HTTPException...\nrouter = APIRouter()...",
        "src/models/user.py": "from sqlalchemy import Column, String...\nclass User(Base):..."
      },
      "test_commands": [
        "pytest tests/test_auth.py -v"
      ],
      "expected_output": "5 passed",
      "template_dir": "./task_templates/web_backend_easy_001",
      "tags": ["python", "fastapi", "authentication"],
      "max_turns": 50,
      "timeout_seconds": 300.0
    }
  ]
}
```

### 关键字段说明

| 字段 | 说明 | 必填 |
|------|------|------|
| `id` | 唯一标识 | 是 |
| `prompt` | 任务描述（给 agent 的指令） | 是 |
| `domain` | 领域标签（`web-backend` 等） | 推荐 |
| `type` | `fix_bug` / `add_feature` / `refactor` / `write_tests` / `implement_from_spec` | 推荐 |
| `difficulty` | `easy` / `medium` / `hard` | 推荐 |
| `ground_truth_files` | 期望的文件内容（`{路径: 内容}`） | RL 阶段需要 |
| `test_commands` | 验证命令（返回 0 = 通过） | RL 阶段需要 |
| `template_dir` | 沙箱模板（项目骨架） | 推荐 |
| `max_turns` | Agent 最大对话轮数 | 否（默认 50） |
| `timeout_seconds` | 超时时间 | 否（默认 300） |

### 模板目录结构

```
task_templates/web_backend_easy_001/
├── src/
│   ├── __init__.py
│   ├── main.py          # FastAPI app 骨架（缺少 auth 路由）
│   └── models/
│       └── __init__.py  # 空文件（等待实现 User 模型）
├── tests/
│   └── test_auth.py     # 测试用例（模板状态下会失败）
├── requirements.txt
└── README.md            # 项目说明
```

**验证原则**：
- 在模板目录直接跑 `test_commands` → 必须**失败**（证明任务确实需要修复）
- 填入 `ground_truth_files` 后再跑 → 必须**通过**（证明参考答案正确）

### 使用 DomainConfig

```python
from claw.training.domain_config import default_registry

reg = default_registry()
web_config = reg.get("web-backend")

# 注入到 lifecycle skill prompt
ctx = web_config.to_prompt_context()
# 输出：
# **Domain**: Web Backend (REST API)
# **Tech Stack**: framework=FastAPI, database=PostgreSQL, ...
# **Review Criteria**: SQL injection prevention, Input validation...
# **Acceptance Checklist**: OpenAPI docs generated, All tests pass...
```

---

## Rollout 执行

### 单任务执行

```bash
# JSON 字面量
claw train \
  --task '{"id":"test_001","prompt":"Fix the null pointer bug","type":"fix_bug","template_dir":"./templates/test_001"}' \
  --temperature 0.0 --seed 42

# 输出：
# Task: test_001 | Reward: 0.85 | Stop: completed | Time: 45.2s
```

### 批量执行

```bash
# 从 JSON 文件加载任务套件
claw train \
  --suite task_suites/web_backend_easy.json \
  --workers 4 \           # 4 进程并行
  --temperature 0.0 \
  --seed 42 \
  --output trajectories.jsonl

# 查看统计
claw train-stats --input trajectories.jsonl
```

输出统计示例：

```
Task Suite Summary
==================
Total Episodes:   25
Completed:        20 (80.0%)
Budget Exceeded:   3 (12.0%)
Errors:            2 (8.0%)
Mean Reward:       0.72
Mean Time:         48.3s
Total Tokens:      1,245,000
```

### 代码调用

```python
from claw.training import (
    RolloutRunner, RolloutConfig,
    TaskSuite, CodingTask,
    DomainConfig,
)
from claw.training.reviewer import ReviewerAgent
from claw.training.slime_adapter import SlimeDataAdapter

# 1. 配置
config = RolloutConfig(
    temperature=0.0,
    max_turns=50,
    num_workers=4,
    seed=42,
)

# 2. 加载任务
suite = TaskSuite.load_from_json("task_suites/web_backend_easy.json")
easy_tasks = suite.get_tasks_by_difficulty("easy")

# 3. 执行 rollout（按难度课程学习）
runner = RolloutRunner(config=config, model_name="qwen3-coder-30b")
results = runner.run_curriculum(suite)  # easy → medium → hard

# 4. 导出统计数据
summary = runner.summary(results)
print(f"Mean reward: {summary['mean_reward']:.3f}")
```

---

## SLIME 集成：何时接入、怎么接入

### 架构边界

```
┌─ Claw Code Agent ─┐          ┌──── SLIME ────┐
│                    │          │                │
│  RolloutRunner     │──JSONL──►│  Data Buffer   │
│  (数据生产)         │          │  (数据消费)     │
│                    │          │       │        │
│  ReviewerAgent     │ reward   │       ▼        │
│  SandboxManager    │──函数──►│  Megatron-LM   │
│  (reward 计算)      │          │  (训练)        │
│                    │          │       │        │
└────────────────────┘          │       ▼        │
                                │  SGLang        │
                                │  (推理/rollout) │
                                └────────────────┘
```

**Claw Code Agent 不调用 SLIME，SLIME 不调用 Claw Code Agent。**
两者通过文件（JSONL）和函数脚本（`slime_custom_rm.py`）解耦。

### 三阶段接入时机

```
Stage 1: SFT Cold Start
─────────────────────────────────────────────
何时: 训练开始时，需要给模型一个合理的初始策略
数据: 强模型（如 Claude）跑 lifecycle rollout
      → 筛选 reward ≥ 0.8 的 trajectory
      → 导出 prompt→response 对（SFT 格式）
接入: claw train → 导出 sft_data.jsonl
      slime --mode sft --data sft_data.jsonl

Stage 2: Reinforcement Learning
─────────────────────────────────────────────
何时: SFT 后，需要让模型通过试错优化
数据: 当前模型（on-policy）或历史模型（off-policy）
      跑 lifecycle rollout
      → 保留所有 trajectory（含负样本）
      → 导出 prompt→response→reward 三元组
接入: claw train → 导出 rl_data.jsonl
      slime --mode rl --data rl_data.jsonl \
            --custom-rm-path ./slime_custom_rm.py

Stage 3: Data Flywheel
─────────────────────────────────────────────
何时: RL 后，需要持续提升
循环: 训练后模型 → 跑更多/更难 rollout
      → Reviewer + Sandbox 评分
      → 高质量数据回灌 SFT/RL 训练集
      → 重新训练 → 循环
接入: 与 Stage 1/2 相同，只是数据源从强模型变为训练后的模型
```

### SLIME 命令示例

```bash
# ===== Stage 1: SFT Cold Start =====
# 1. 用强模型生成 SFT 数据
claw train \
  --suite task_suites/all_domains_easy.json \
  --temperature 0.0 --seed 42 \
  --output sft_cold_start.jsonl

# 2. 筛选高质量 trajectory
python3 -c "
from claw.training.slime_adapter import SlimeDataAdapter
import json
with open('sft_cold_start.jsonl') as f:
    results = [json.loads(line) for line in f]
count = SlimeDataAdapter.export_sft_dataset(
    results, 'sft_filtered.jsonl', min_reward=0.8
)
print(f'Exported {count} high-quality samples')
"

# 3. 用 slime 做 SFT
slime --mode sft \
  --model-path /path/to/base-model \
  --data sft_filtered.jsonl \
  --output-dir ./checkpoints/sft_cold_start \
  --epochs 3 \
  --learning-rate 1e-5

# ===== Stage 2: RL Training =====
# 1. 用 SFT 后的模型跑 rollout
claw train \
  --suite task_suites/all_domains_medium.json \
  --model sft-checkpoint \
  --temperature 0.0 --seed 42 \
  --workers 8 \
  --output rl_rollouts.jsonl

# 2. 用 slime 做 RL（GRPO）
slime --mode rl \
  --model-path ./checkpoints/sft_cold_start \
  --data rl_rollouts.jsonl \
  --custom-rm-path ./slime_custom_rm.py \
  --algorithm grpo \
  --output-dir ./checkpoints/rl_grpo \
  --num-epochs 1 \
  --kl-coef 0.04

# ===== Stage 3: Data Flywheel =====
# 1. 用训练后模型跑更多 rollout
claw train \
  --suite task_suites/all_domains_hard.json \
  --model ./checkpoints/rl_grpo \
  --temperature 0.0 --seed 123 \
  --workers 8 \
  --output flywheel_round1.jsonl

# 2. 筛选高质量数据回灌
python3 -c "
from claw.training.slime_adapter import SlimeDataAdapter
import json
with open('flywheel_round1.jsonl') as f:
    results = [json.loads(line) for line in f]
# 质量筛选
filtered = SlimeDataAdapter.filter_by_quality(
    results, min_reward=0.9, min_review_score=0.7
)
# 回灌 SFT 集
SlimeDataAdapter.export_sft_dataset(filtered, 'flywheel_sft.jsonl')
# 回灌 RL 集（保留部分负样本多样性）
SlimeDataAdapter.export_rl_dataset(results, 'flywheel_rl.jsonl')
"

# 3. 继续训练
slime --mode rl \
  --model-path ./checkpoints/rl_grpo \
  --data flywheel_rl.jsonl \
  --custom-rm-path ./slime_custom_rm.py \
  --algorithm grpo \
  --output-dir ./checkpoints/flywheel_round1
```

---

## On-Policy vs Off-Policy

### 概念对比

| | On-Policy | Off-Policy |
|---|---|---|
| **定义** | 用**当前正在训练的模型**生成 rollout 数据 | 用**任意模型**（历史 checkpoint、强模型等）生成数据 |
| **数据时效性** | 高（反映当前策略） | 低（可能来自旧策略） |
| **训练效率** | 低（每轮训练前需要重新 rollout） | 高（数据可以预先收集、复用） |
| **适用算法** | PPO、GRPO、GSPO | DPO、SFT、Rejection Sampling |
| **在 slime 中的用法** | `slime --mode rl` 同时启动 Megatron + SGLang，在线采样 | `slime --mode sft` 或手动提供预收集的 JSONL |

### 本项目推荐策略

```
        Off-Policy (SFT Cold Start)       On-Policy (RL GRPO)
        ─────────────────────────         ───────────────────
        用强模型预生成高质量轨迹            用当前模型在线生成
        ↓                                  ↓
        训练基础模型学会"流程"              强化模型追求"高质量产出"
        ↓                                  ↓
        └────────────┬─────────────────────┘
                     ↓
              Data Flywheel
              (off-policy 质量筛选 + on-policy RL)
```

**为什么先 Off-Policy 再 On-Policy**：
1. **冷启动阶段（Off-Policy SFT）**：基础模型不懂 lifecycle 流程，直接 RL 探索效率极低。先用强模型生成的"正确答案"轨迹做 SFT，让模型建立基本的流程意识。
2. **RL 阶段（On-Policy）**：SFT 后的模型已经知道要按流程走，但产出质量不高。用 On-Policy RL 让它通过 reward 信号自我改进。
3. **飞轮阶段（混合）**：训练后的模型生成数据 → 筛选高质量样本 → 回灌 SFT 训练集（Off-Policy）+ 新一轮 RL（On-Policy）。

### 何时用 Off-Policy

| 场景 | 说明 |
|------|------|
| **SFT Cold Start** | 用强模型数据教会基础模型流程 |
| **数据增强** | 用历史高质量 trajectory 扩充训练集 |
| **域迁移** | 在新领域没有 on-policy 数据时，先用其他领域的 off-policy 数据预热 |
| **Rejection Sampling** | 生成大量候选，只保留 reward 最高的 |

### 何时用 On-Policy

| 场景 | 说明 |
|------|------|
| **GRPO/PPO 训练** | RL 算法要求数据来自当前策略 |
| **探索新策略** | 模型需要在当前能力边界外尝试新的解法 |
| **飞轮迭代** | 每轮训练后用新模型重新生成数据 |

---

## 评测分离：Reviewer Agent

### 为什么需要独立评测

Test-based reward（`test_pass_rate + diff_accuracy`）只测功能正确性，不测：
- 代码质量（可读性、命名、SOLID）
- 安全性（SQL 注入、XSS、权限）
- 架构合理性（是否遵循领域最佳实践）
- 测试覆盖（边界情况、错误路径）

而且如果让 Work Agent 自己审查自己的代码，会有**自我评估偏差**——它倾向于给自己的代码打高分。

### Reviewer 工作流程

```
Work Agent 产出:
  - write_file 记录 {路径: 内容}
  - test_commands 执行结果
  - 架构文档

        ↓

Reviewer Agent（独立 Session, 独立 System Prompt）:
  - "你是一个严格的代码审查专家"
  - 逐维度打分（0.0-1.0）
  - 逐问题列出（severity + file + description + suggestion）

        ↓

Combined Reward:
  reward = test_pass_rate × 0.4
         + diff_accuracy × 0.3
         + review_score × 0.3
```

### 配置 Reviewer

```python
from claw.training.reviewer import ReviewerAgent

reviewer = ReviewerAgent(
    model_config=reviewer_model_config,  # 可用更强模型
    cwd="/tmp/review_sandbox",
    criteria=[
        "**security**: SQL injection, XSS, auth bypass — any exploitable vulnerability is critical",
        "**code_quality**: Readability, DRY, SOLID, error handling",
        "**performance**: N+1 queries, blocking I/O, missing indexes",
    ],
)

# 收集 work agent 的产出
work_output = {
    "task_prompt": task.prompt,
    "files": {"src/routes/auth.py": "...", "src/models/user.py": "..."},
    "test_results": "5 passed in 2.34s",
    "architecture": "Monorepo with FastAPI backend...",
}

# 执行审查
report = reviewer.review(**work_output)
print(f"Review score: {report.overall_score}")
print(f"Critical issues: {report.critical_count()}")
for issue in report.issues:
    print(f"  [{issue.severity}] {issue.dimension}: {issue.description}")
```

### Reviewer 在 SLIME 中的角色

在 SLIME RL 训练中，Reviewer 的评分**不直接参与** on-policy reward 计算（因为 SLIME rollout 时 Reviewer 不能在线调用 API）。替代方案：

1. **Offline Review**（推荐）：Rollout 完成后，用 Reviewer 对所有 trajectory 打分，review_score 合并进 JSONL 的 `reward` 字段
2. **参考信号**：Reviewer 评分用于数据筛选（飞轮中的质量门），而不是 RL 训练中的即时 reward
3. **slime_custom_rm.py 只用 test/diff**：`slime_custom_rm.py` 是纯文本 reward 函数，只跑测试和 diff，不调用 Reviewer（因为 slime 训练循环不能发 API 请求）

---

## 数据飞轮：三阶段迭代

### 飞轮架构

```
                    ┌──────────────────┐
                    │   强模型 Rollout   │
                    │  (Claude / GPT)   │
                    └────────┬─────────┘
                             │ 高质量 trajectory
                             ▼
                    ┌──────────────────┐
                    │  SFT Cold Start  │  ← Stage 1
                    │  (流程学习)       │
                    └────────┬─────────┘
                             │ 基础模型
                             ▼
        ┌──────────────────────────────────────┐
        │            RL Training                │  ← Stage 2
        │  ┌─────────┐     ┌─────────────┐     │
        │  │ Rollout │────►│ Test+Review │     │
        │  │ (SGLang)│     │ (reward)    │     │
        │  └─────────┘     └──────┬──────┘     │
        │         ▲               │             │
        │         │  params       │  data       │
        │  ┌──────┴──────┐  ┌────▼────────┐    │
        │  │  Megatron   │◄─┤ Data Buffer │    │
        │  └─────────────┘  └─────────────┘    │
        └──────────┬───────────────────────────┘
                   │ 训练后模型
                   ▼
        ┌──────────────────────────────────────┐
        │         Data Flywheel                 │  ← Stage 3
        │                                       │
        │  训练后模型 → 新领域/高难度 rollout    │
        │       │                               │
        │       ▼                               │
        │  Reviewer 筛选 (reward ≥ 0.9)          │
        │       │                               │
        │       ├── 回灌 SFT 集（质量提升）       │
        │       └── 回灌 RL 集（多样性保持）      │
        │       │                               │
        │       ▼                               │
        │  继续 RL 训练 → 循环                   │
        └──────────────────────────────────────┘
```

### 每轮飞轮的评估指标

| 指标 | 说明 | 目标 |
|------|------|------|
| **Mean Reward** | 所有任务的平均 reward | 每轮上升 ≥ 0.05 |
| **Completion Rate** | `stop_reason=completed` 比例 | 每轮上升 ≥ 5% |
| **Review Score** | Reviewer 平均分 | 每轮上升 ≥ 0.03 |
| **Cross-Domain Gap** | 训练领域 vs 未见领域的 reward 差 | 每轮缩小 |
| **Critical Issues** | Reviewer 发现的 critical 问题数 | 每轮下降 |

### 飞轮停止条件

- Mean Reward 连续 2 轮不提升
- Cross-Domain Gap < 0.1（泛化达到瓶颈）
- 或达到预设的迭代轮数上限

---

## 完整示例：端到端训练流程

### 场景：训练一个能按软件工程流程开发 Web 后端的模型

```bash
#!/bin/bash
set -e

# ============================================================
# 准备工作
# ============================================================

# 1. 环境
uv sync
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local-token
export OPENAI_MODEL=qwen3-coder-30b

# 2. 准备任务套件（或使用已生成的）
mkdir -p task_suites

# ============================================================
# Stage 1: SFT Cold Start（Off-Policy）
# ============================================================

echo "=== Stage 1: SFT Cold Start ==="

# 用强模型生成 easy 任务的冷启动数据
claw train \
  --suite task_suites/web_backend_easy.json \
  --temperature 0.0 --seed 42 \
  --workers 4 \
  --output sft_cold_start.jsonl

# 查看统计
claw train-stats --input sft_cold_start.jsonl

# 筛选 reward ≥ 0.8 的高质量样本
python3 -c "
from claw.training.slime_adapter import SlimeDataAdapter
import json

with open('sft_cold_start.jsonl') as f:
    results = [json.loads(line) for line in f]

count = SlimeDataAdapter.export_sft_dataset(
    results, 'sft_filtered.jsonl', min_reward=0.8
)
print(f'SFT samples: {count}/{len(results)}')
"

# SFT 训练（需要已安装 slime）
# slime --mode sft \
#   --model-path /path/to/base-model \
#   --data sft_filtered.jsonl \
#   --output-dir ./checkpoints/sft_cold_start \
#   --epochs 3 --learning-rate 1e-5

# ============================================================
# Stage 2: RL Training（On-Policy GRPO）
# ============================================================

echo "=== Stage 2: RL Training ==="

# 用 SFT checkpoint 跑 medium 任务的 rollout
claw train \
  --suite task_suites/web_backend_medium.json \
  --temperature 0.0 --seed 123 \
  --workers 8 \
  --output rl_rollouts.jsonl

# RL 训练
# slime --mode rl \
#   --model-path ./checkpoints/sft_cold_start \
#   --data rl_rollouts.jsonl \
#   --custom-rm-path ./slime_custom_rm.py \
#   --algorithm grpo \
#   --output-dir ./checkpoints/rl_grpo \
#   --kl-coef 0.04

# ============================================================
# Stage 3: Data Flywheel（迭代提升）
# ============================================================

echo "=== Stage 3: Data Flywheel Round 1 ==="

# 用 RL 后的模型跑 hard 任务
claw train \
  --suite task_suites/web_backend_hard.json \
  --temperature 0.0 --seed 456 \
  --workers 8 \
  --output flywheel_r1.jsonl

# 质量筛选 + 回灌
python3 -c "
from claw.training.slime_adapter import SlimeDataAdapter
import json

with open('flywheel_r1.jsonl') as f:
    results = [json.loads(line) for line in f]

# 筛选高质量样本
filtered = SlimeDataAdapter.filter_by_quality(
    results, min_reward=0.9, min_review_score=0.7
)
print(f'Flywheel round 1: {len(filtered)}/{len(results)} high quality')

# 回灌
SlimeDataAdapter.export_sft_dataset(filtered, 'flywheel_r1_sft.jsonl')
SlimeDataAdapter.export_rl_dataset(results, 'flywheel_r1_rl.jsonl')
"

# 继续 RL 训练
# slime --mode rl \
#   --model-path ./checkpoints/rl_grpo \
#   --data flywheel_r1_rl.jsonl \
#   --custom-rm-path ./slime_custom_rm.py \
#   --algorithm grpo \
#   --output-dir ./checkpoints/flywheel_r1

# ============================================================
# 评估：跨领域泛化
# ============================================================

echo "=== Cross-Domain Evaluation ==="

# 用训练好的模型跑未见过的领域（如 cli-tool）
claw train \
  --suite task_suites/cli_tool_medium.json \
  --temperature 0.0 --seed 999 \
  --workers 4 \
  --output cross_domain_eval.jsonl

claw train-stats --input cross_domain_eval.jsonl

echo "Done!"
```

---

## 配置参考

### RolloutConfig 完整参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `temperature` | float | 0.0 | 模型温度（训练时建议 0.0） |
| `max_turns` | int | 50 | 每个 episode 最大对话轮数 |
| `timeout_seconds` | float | 600.0 | 每个 episode 超时 |
| `num_workers` | int | 1 | 并行 worker 数（>1 用 multiprocessing） |
| `seed` | int | 42 | 随机种子 |
| `session_prefix` | str | "train" | Session 命名前缀 |
| `disabled_runtimes` | List[str] | [] | 禁用的运行时列表 |

### DeterministicConfig 完整参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `temperature` | float | 0.0 | 固定温度 |
| `session_id` | str | - | 固定 session ID |
| `disabled_runtimes` | List[str] | [] | 要禁用的运行时 |
| `seed` | int | 42 | RNG 种子 |

### DomainConfig 完整参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | str | - | 唯一标识（如 `web-backend`） |
| `display_name` | str | "" | 显示名称 |
| `description` | str | "" | 描述 |
| `tech_stack` | Dict[str,str] | {} | 技术栈（注入 ARCHITECTURE 阶段） |
| `recommended_patterns` | List[str] | [] | 推荐模式 |
| `project_structure` | str | - | 建议的目录结构 |
| `coding_standards` | List[str] | [] | 编码规范 |
| `test_framework` | str | "pytest" | 测试框架 |
| `test_template` | str | "" | 测试文件模板 |
| `review_criteria` | List[str] | [] | 审查标准（注入 CODE_REVIEW 阶段） |
| `acceptance_checklist` | List[str] | [] | 验收清单（注入 ACCEPTANCE 阶段） |
| `skill_overrides` | Dict[str,str] | {} | 每个阶段的 skill prompt 覆盖 |

### 内置领域速查

| 领域 | 框架 | 测试 | 审查重点 |
|------|------|------|---------|
| `web-backend` | FastAPI + PostgreSQL + SQLAlchemy | pytest + httpx | SQL 注入、输入验证、N+1 查询 |
| `web-frontend` | React + TypeScript + Tailwind | vitest + testing-library | XSS 防护、可访问性、重渲染优化 |
| `cli-tool` | Python + click | pytest + CliRunner | 参数验证、退出码、管道兼容 |
| `data-pipeline` | Python + pandas/polars | pytest + fixtures | 空值处理、类型一致性、内存效率 |
| `sdk-library` | Python + Sphinx | pytest + doctest | 公共 API 设计、错误信息、向后兼容 |
