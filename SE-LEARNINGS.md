# 软件工程知识点参考

本文档梳理软件工程开发中的核心知识点，按主题分章节展开。每个主题先讲**通用概念、原则、权衡**，再以本仓库（ClawCodeAgent）作为案例锚点（标记为「📍 项目对应」）。本文档以软件工程为主，项目仅作示例。

---

## 目录

1. [架构与设计](#一架构与设计)
2. [模块化与关注点分离](#二模块化与关注点分离)
3. [API 设计](#三api-设计)
4. [配置与环境](#四配置与环境)
5. [依赖管理](#五依赖管理)
6. [CLI 设计](#六cli-设计)
7. [状态管理与持久化](#七状态管理与持久化)
8. [并发、异步与流式](#八并发异步与流式)
9. [测试策略](#九测试策略)
10. [错误处理与可观测性](#十错误处理与可观测性)
11. [安全](#十一安全)
12. [性能与资源管理](#十二性能与资源管理)
13. [构建与分发](#十三构建与分发)
14. [版本控制与协作](#十四版本控制与协作)
15. [代码质量与重构](#十五代码质量与重构)
16. [文档](#十六文档)
17. [机器学习工程特化](#十七机器学习工程特化)

---

## 一、架构与设计

### 1.1 关注点：架构是关于"边界"的

软件架构的本质是**划分边界**：哪些东西可以一起改动，哪些必须独立演化。好的架构让"高频变化的部分"不会牵动"低频稳定的部分"。

#### 知识点

- **稳定依赖原则 (SDP, Stable Dependencies Principle)**：依赖应当指向更稳定的方向。常变的代码依赖少变的代码，反之则脆弱。
- **稳定抽象原则 (SAP)**：越稳定的模块越应该抽象。具体实现易变，抽象接口应稳定。
- **依赖倒置 (DIP)**：高层模块不依赖低层实现，两者都依赖抽象。
- **松耦合 / 高内聚**：模块内部强相关、模块之间弱相关。判断标准：删除一个模块时，受影响的文件数量。

#### 常见架构风格

| 风格 | 特征 | 适用场景 |
|---|---|---|
| **分层 (Layered)** | UI / Service / Data 分三层 | 业务清晰、CRUD 为主 |
| **六边形 (Hexagonal / Ports-and-Adapters)** | 核心业务被适配器包围 | 多入口（CLI/HTTP/MQ）|
| **微服务** | 进程边界 = 模块边界 | 团队规模大、独立部署需求 |
| **事件驱动** | 通过事件解耦组件 | 异步、削峰、跨域集成 |
| **管道-过滤器** | 数据流过一系列变换 | 编译器、数据 ETL |

#### 反模式

- **大泥球 (Big Ball of Mud)**：所有东西互相依赖
- **God Object**：一个类承担过多职责
- **架构过度设计**：5 人小团队照搬大厂微服务，运维成本爆炸

📍 **项目对应**：核心 `LocalCodingAgent` 是六边形风格——中心是 agent 循环，外圈是可拔插的 `*_runtime.py`、工具注册表、模型客户端。

---

### 1.2 设计模式

模式不是教条，是**沟通词汇**。会用之前先理解它解决的问题，否则容易过度设计。

#### 创建型

- **Factory / Abstract Factory**：把"new"封装起来，便于切换实现
- **Builder**：分步构造复杂对象，避免长参数列表
- **Singleton**：全局唯一实例。**警告**：单例几乎总是依赖注入的弱替代品；测试不友好

#### 结构型

- **Adapter**：包装第三方接口，让它符合自己的协议
- **Decorator**：给对象包一层增强行为（如缓存、日志、重试）
- **Facade**：给复杂子系统一个简单入口
- **Proxy**：访问真实对象前的代理层（懒加载、权限）

#### 行为型

- **Strategy**：把可变算法抽出来，运行时切换
- **Observer / Pub-Sub**：状态变化广播给订阅者
- **Command**：把请求封装成对象（支持撤销、队列）
- **State / 状态机**：对象行为随状态切换
- **Chain of Responsibility**：请求沿处理链传递

#### 反模式

- **强行套模式**：简单两个 if 硬上 Strategy
- **过度抽象**：还没有第二个实现就抽象出接口

📍 **项目对应**：
- Adapter：`OpenAICompatClient` / `AnthropicClient` 适配两种 API 格式
- State：`LifecycleRuntime` 是 10 阶段状态机
- Strategy：`SlimeDataAdapter.export_sft_dataset` vs `export_rl_dataset` 是不同的导出策略
- Command：`ToolCall` 是请求对象，可序列化、可重放

---

### 1.3 SOLID

- **S** - Single Responsibility：一个类只有一个变化原因
- **O** - Open/Closed：对扩展开放，对修改关闭（通过加新代码而非改老代码扩展功能）
- **L** - Liskov Substitution：子类必须能无缝替换父类
- **I** - Interface Segregation：客户端不应被迫依赖它不用的方法
- **D** - Dependency Inversion：依赖抽象而非具体

SOLID 是面向对象的设计指南，但**核心思想适用于函数式、模块化代码**。比如"S"在函数里就是"一个函数只做一件事"。

---

### 1.4 DRY / KISS / YAGNI / Separation of Concerns

- **DRY (Don't Repeat Yourself)**：同一个知识只表达一次。但**警惕过度 DRY**——把不相关的相似代码强行合并会产生错误耦合。Sandi Metz 名言：「重复 < 错误抽象」。
- **KISS (Keep It Simple, Stupid)**：能用简单方案别上花活。
- **YAGNI (You Aren't Gonna Need It)**：不要为想象中的需求写代码。
- **SoC (Separation of Concerns)**：不同关注点（业务逻辑、IO、配置）分文件/模块。

---

## 二、模块化与关注点分离

### 2.1 模块边界划分原则

- **按变化频率划分**：经常一起改的放一起，独立演化的分开
- **按抽象层次划分**：高层策略 vs 低层机制
- **按团队边界划分**（Conway's Law）：模块结构会反映组织结构

#### 模块化的衡量标准

- **耦合度 (Coupling)**：从高到低
  - Content（内容耦合，A 改 B 内部状态）❌
  - Common（共享全局变量）❌
  - Control（A 控制 B 的执行流）⚠️
  - Stamp（共享数据结构）⚠️
  - Data（仅传参数）✅
  - Message（消息传递）✅
- **内聚度 (Cohesion)**：从低到高
  - Coincidental（偶然放一起）❌
  - Logical（按类型分组，如所有 IO）⚠️
  - Temporal（同一时间执行）⚠️
  - Procedural / Communicational（按流程）✅
  - Sequential / Functional（紧密协作完成单一任务）✅

### 2.2 接口设计

- **小接口胜过大接口**（ISP）
- **方法名表达意图，参数名表达内容**
- **命令-查询分离 (CQS)**：方法要么修改状态（命令）、要么返回结果（查询），不要兼顾
- **避免布尔参数**：`do(true)` 不可读；用枚举或拆成两个方法
- **避免位置参数过多**：超过 3 个考虑用对象/字典

### 2.3 依赖注入 (DI)

- **构造器注入**（推荐）：依赖在创建时传入
- **属性注入**：构造后赋值（破坏不变性）
- **方法注入**：每次调用时传入

DI 的价值：测试时可以注入 mock，无需修改被测代码。

📍 **项目对应**：`AgentEnv(sandbox_manager=..., model_name=...)` 通过构造器注入沙箱管理器；测试时直接构造 `FakeOpenAIClient` 替换。

---

## 三、API 设计

### 3.1 通用原则

- **难以误用** > 容易使用：好 API 让错误用法编译/类型检查不通过
- **最小惊讶原则 (Principle of Least Astonishment)**：行为符合直觉
- **正交性**：不同功能互相独立，组合使用不冲突
- **不变性优先**：返回不可变对象/副本，避免调用方意外修改

### 3.2 HTTP / REST API

- **资源命名用复数名词**：`/users`、`/orders`，不要 `/getUser`
- **HTTP 动词承载语义**：GET / POST / PUT / PATCH / DELETE
- **状态码语义化**：2xx 成功 / 4xx 客户端错 / 5xx 服务端错
- **幂等性**：GET / PUT / DELETE 应幂等，POST 不必
- **版本化**：URL 版本（`/v1/...`）或 Header 版本
- **分页**：基于 cursor 优于基于 offset（避免数据移动导致重复/遗漏）
- **错误响应统一格式**：`{error: {code, message, details}}`

### 3.3 库 API（语言级）

- **错误处理风格**：异常 vs 返回 Result/Either
- **同步 vs 异步**：避免 sync 函数内部偷偷调用 async（colored function 问题）
- **配置对象优于多参数**：`def f(opts: Options)` > `def f(a, b, c, d, e)`
- **不要泄漏内部类型**：库的公共类型应稳定，内部实现可变

### 3.4 向后兼容

- **加是安全的，删/改是危险的**
- **弃用流程**：标记 → 警告 → 一个版本周期后删除
- **语义化版本 (SemVer)**：MAJOR.MINOR.PATCH
  - MAJOR：破坏性变更
  - MINOR：向后兼容的新功能
  - PATCH：向后兼容的 bug 修复

📍 **项目对应**：`agent_types.py` 用 `dataclass` + `to_dict/from_dict` 自定义序列化，便于版本演进时兼容旧字段。

---

## 四、配置与环境

### 4.1 12-Factor App 中的配置原则

- **配置与代码分离**：不要把 API key 硬编码
- **配置优先级链**：默认值 → 配置文件 → 环境变量 → CLI 参数（后者覆盖前者）
- **每个环境一份配置**：dev / staging / prod 不要混用
- **敏感配置走专用通道**：用 secret manager（Vault、AWS Secrets Manager）而非环境变量打 log

### 4.2 配置存储形式对比

| 形式 | 优点 | 缺点 |
|---|---|---|
| 环境变量 | 简单、12-factor 友好 | 不结构化、容易泄漏 |
| `.env` 文件 | 本地开发方便 | 不能放仓库，需 `.env.example` |
| YAML / TOML / JSON | 结构化、可注释（YAML/TOML） | 需要解析、容易格式错误 |
| Key-value 服务（Consul/etcd） | 动态更新、集中管理 | 增加运维负担 |
| 代码内（const） | 简单 | 修改要重编 |

### 4.3 Feature Flag

- **运行时开关**控制功能启停，无需重新部署
- 用途：灰度发布、A/B 测试、应急关闭
- 工具：LaunchDarkly、Unleash、自建

### 4.4 配置校验

- **启动时校验**：缺失/非法配置立即失败（fail-fast），别等用到时才报错
- **schema 校验**：JSON Schema、Pydantic、dataclass + 类型注解
- **默认值要安全**：默认应是最保守的（如 `allow_write=False`）

📍 **项目对应**：`api_config.py` 实现"默认 → JSON 配置 → .env → 环境变量"四层优先级链；`AgentPermissions` 默认 `allow_write=False`。

---

## 五、依赖管理

### 5.1 依赖最少化

- **每个依赖都是债务**：要 review 安全更新、breaking change、维护状态
- **核心库零依赖更易分发**：用户安装阻力小
- **运行时依赖 vs 开发依赖** 必须分开：`dev` extras / `[dependency-groups]`

### 5.2 锁定文件 (Lock File)

- **作用**：确定性安装（同样的 lock 装出同样的版本）
- **Python**：`uv.lock`、`poetry.lock`、`Pipfile.lock`
- **Node**：`package-lock.json`、`yarn.lock`、`pnpm-lock.yaml`
- **Rust**：`Cargo.lock`
- **规则**：库的 lock 不进仓库（被使用方约束），应用的 lock 必进仓库

### 5.3 版本约束策略

- **精确锁定 (`==1.2.3`)**：可复现，但更新慢
- **兼容版本 (`~=1.2.3` 或 `^1.2.3`)**：允许 patch/minor 更新
- **范围 (`>=1.2,<2.0`)**：明确兼容范围

### 5.4 依赖审计

- **CVE 扫描**：`pip-audit`、`npm audit`、`cargo audit`、Dependabot
- **许可合规**：GPL/AGPL 在商业项目要小心
- **传递依赖**：A 依赖 B，B 依赖 C，C 出问题时也要知道

### 5.5 可选依赖 (Extras)

把"非核心功能"做成可选安装：
```toml
[project.optional-dependencies]
web = ["fastapi", "uvicorn"]
ml = ["torch", "transformers"]
```
用户按需 `pip install pkg[web]`。

📍 **项目对应**：核心 `dependencies = []` 完全零依赖；`tui` / `web` / `dev` 三个 extras 各自隔离。

---

## 六、CLI 设计

### 6.1 良好 CLI 的特征

- **能管道化**：输出 stdout，错误 stderr，遵循 UNIX 哲学
- **退出码语义化**：0 成功，非 0 失败（不同失败用不同码）
- **支持 `--help` 和 `--version`**
- **子命令组织**：复杂工具用 `tool subcommand args`（git/docker 风格）
- **明确的 stdin 行为**：要交互式 vs 管道输入

### 6.2 参数风格

- **POSIX 风格**：短选项 `-x`，长选项 `--xxx`
- **GNU 风格**：长选项可以 `--xxx=value` 或 `--xxx value`
- **避免位置参数过多**：超过 2 个就考虑用 flag

### 6.3 输出设计

- **机器可读 vs 人可读**：人看的彩色表格 + `--json` 输出 JSON
- **TTY 检测**：`isatty(stdout)` 判断是否管道，自动关闭颜色和进度条
- **进度反馈**：长任务要有进度条或日志

### 6.4 配置层级

CLI 参数 > 环境变量 > 配置文件 > 内置默认。

### 6.5 工具

- Python：`argparse`（标准库）、`click`（更友好）、`typer`（基于类型注解）
- Node：`commander`、`yargs`
- Rust：`clap`
- Go：`cobra`

📍 **项目对应**：`main.py` 用 `argparse` 子命令；`claw train`、`claw train-stats`、`claw train-web` 各自独立 cmd 函数返回 exit code。

---

## 七、状态管理与持久化

### 7.1 状态分类

- **临时态**：仅运行时存在（缓存、会话）
- **持久态**：跨重启保留（数据库、文件）
- **派生态**：从其他状态计算得出（不应直接持久化）
- **共享态**：多实例共享（分布式锁、消息队列）

### 7.2 不变性 (Immutability)

- **优点**：无副作用、易并发、易缓存、易调试
- **缺点**：内存开销、需要"修改时复制"
- **Append-only / Event Sourcing**：状态变更只追加，从不原地修改
  - 优点：可审计、可时间旅行、可重放
  - 缺点：存储增长快、查询需要重建状态

### 7.3 持久化模式

| 模式 | 适用场景 |
|---|---|
| **关系数据库**（强一致、ACID） | 业务核心数据 |
| **文档数据库**（MongoDB） | 半结构化、schema 演进频繁 |
| **KV 存储**（Redis） | 缓存、会话、排行榜 |
| **列式存储**（ClickHouse） | OLAP / 分析 |
| **文件系统** | 大对象、流式日志 |
| **对象存储**（S3） | 海量、廉价 |
| **追加日志**（Kafka） | 事件流 |

### 7.4 ACID vs BASE

- **ACID**（关系库）：原子、一致、隔离、持久
- **BASE**（NoSQL）：基本可用、软状态、最终一致

CAP 定理：一致性 / 可用性 / 分区容忍三选二（实际是 CP vs AP）。

### 7.5 事务与隔离级别

- **Read Uncommitted** → 脏读
- **Read Committed** → 不可重复读
- **Repeatable Read** → 幻读（MySQL 默认）
- **Serializable** → 完全隔离（最慢）

### 7.6 数据迁移

- **向后兼容的 schema 变更**：先加列（可空）→ 部署代码读新字段 → 删旧字段
- **大表迁移**：用 online schema change 工具（gh-ost、pt-online-schema-change）
- **数据回填**：分批小步走，避免锁表

📍 **项目对应**：`AgentSession` 是 append-only 消息账本；`session_store.py` 持久化到 `.port_sessions/` 目录；阶段快照通过 git commit 实现 time travel 回滚。

---

## 八、并发、异步与流式

### 8.1 并发模型

- **多线程**：共享内存，需要锁；适合 IO 密集
- **多进程**：独立内存，IPC 通信；适合 CPU 密集（绕过 GIL）
- **异步 (async/await)**：单线程事件循环；适合海量 IO 连接
- **协程 (Coroutine)**：用户态轻量任务（Python asyncio、Go goroutine）
- **Actor 模型**：消息驱动的隔离实体（Erlang、Akka）

### 8.2 同步原语

- **Mutex / Lock**：互斥访问临界区
- **Semaphore**：限流，N 个许可
- **Condition Variable**：等待条件成立
- **RWLock**：读多写少时的优化
- **Atomic**：无锁原子操作

### 8.3 常见并发 Bug

- **数据竞争 (Race Condition)**：多线程不同步访问共享数据
- **死锁 (Deadlock)**：循环等待。预防：按固定顺序获取锁
- **活锁 (Livelock)**：不停重试但都进不去
- **饥饿 (Starvation)**：某个线程永远拿不到资源
- **TOCTOU (Time Of Check, Time Of Use)**：检查和使用之间状态变了

### 8.4 异步陷阱

- **颜色函数 (Function Coloring)**：sync 函数不能直接 await async 函数
- **阻塞事件循环**：在 async 里写 `time.sleep` 会卡死所有任务
- **未等待的 Task**：忘记 `await` 导致任务静默丢失
- **取消传播**：父任务取消，子任务需要正确清理

### 8.5 背压 (Backpressure)

生产者比消费者快时怎么办？
- **丢弃**：丢新的（DropTail）或丢旧的（DropHead）
- **阻塞**：生产者等
- **缓冲**：队列（但会增加延迟、占内存）
- **降采样**：只取 1/N

### 8.6 流式处理

- **流 (Stream)**：增量产生/消费数据，无需全部加载
- **Pull vs Push**：消费者拉 vs 生产者推
- **Reactive**：流 + 操作符（map/filter/buffer）

📍 **项目对应**：`RolloutRunner` 用 `multiprocessing.Pool` 并行训练 episode（CPU/IO 都重）；模型客户端有 `complete()` 同步和 `stream()` 流式两条路径。

---

## 九、测试策略

### 9.1 测试金字塔

```
        /\
       /  \    E2E (少量、慢、抓集成 bug)
      /----\
     /      \  Integration (中等、组件协作)
    /--------\
   /          \ Unit (大量、快、纯函数)
  /____________\
```

理想比例：单元 70% / 集成 20% / E2E 10%。倒置（E2E 多）的测试套件运行慢、维护贵、误报多。

### 9.2 测试分类

- **单元测试 (Unit)**：测单个函数/类，无外部依赖
- **集成测试 (Integration)**：测多个组件协作
- **端到端 (E2E)**：从用户视角走完整流程
- **冒烟测试 (Smoke)**：最基本功能能用
- **回归测试 (Regression)**：修过的 bug 不能复发
- **性能测试**：负载、压力、容量
- **安全测试**：渗透、模糊测试 (fuzzing)
- **契约测试 (Contract)**：服务间接口约定

### 9.3 测试替身 (Test Doubles)

- **Dummy**：仅占位，不被使用
- **Stub**：返回硬编码响应
- **Spy**：记录调用信息
- **Mock**：预设期望，验证被怎么调用
- **Fake**：简化但真实的实现（如内存数据库）

选择原则：能用 fake 就别用 mock；mock 容易和实现细节耦合。

### 9.4 测试质量

- **AAA / Given-When-Then**：准备 / 操作 / 断言 三段式
- **每个测试只测一件事**：失败时一眼看出问题
- **测试要独立**：不能依赖执行顺序
- **测试要确定**：不能 flaky（用固定 seed、避免 sleep、注意时区）
- **快速反馈**：单测应在毫秒级
- **可读性优先**：测试是文档

### 9.5 TDD (Test-Driven Development)

红 → 绿 → 重构循环：
1. 写一个失败的测试
2. 写最少代码让它通过
3. 重构

价值不在"先写测试"的仪式，而在**从使用者视角驱动设计**。

### 9.6 覆盖率

- **行覆盖**、**分支覆盖**、**条件覆盖**、**路径覆盖**
- **覆盖率不是目标**：100% 行覆盖也可能漏测边界条件
- **Mutation Testing**：故意改代码看测试能否抓到（如 Stryker、mutmut）

### 9.7 测试隔离技术

- **临时目录** (`tempfile.mkdtemp`) 避免污染
- **数据库回滚** / 内存数据库
- **Docker 容器** 提供干净环境
- **依赖注入** 替换外部服务
- **录制回放 (VCR)**：录制真实 HTTP 响应供离线回放

📍 **项目对应**：`tests/test_training_pipeline_e2e.py` 三档测试齐全——纯函数 reward 计算单测、sandbox+reward 集成、完整 rollout→export 端到端；`FakeOpenAIClient` 是脚本化 fake；每个测试用 `tempfile.mkdtemp` 隔离。

---

## 十、错误处理与可观测性

### 10.1 错误处理风格

- **异常 (Exception)**：Python/Java 主流
  - 优点：不污染正常路径
  - 缺点：调用方可能忘了处理
- **Result 类型**：Rust / Go 风格
  - 优点：类型系统强制处理
  - 缺点：错误处理代码显眼
- **回调 / Promise**：Node.js 早期
  - 已被 async/await 替代

### 10.2 异常设计

- **该捕获什么**：能恢复的、有意义的边界
- **不该捕获什么**：编程错误（NullPointer、IndexError）
- **范围尽量小**：`try` 块里只放可能抛异常的那一行
- **不要吞异常**：`except Exception: pass` 是反模式（除非有明确日志）
- **保留堆栈**：`raise NewError() from old` 而非裸 `raise NewError()`
- **自定义异常类**：表达业务语义（`PaymentDeclinedError` > `Exception("declined")`）

### 10.3 错误恢复策略

- **Fail-Fast**：尽早失败，避免坏数据扩散
- **Fail-Safe**：失败时降级到安全状态（断路器）
- **重试 (Retry)**：指数退避 + 抖动 + 最大次数
- **熔断 (Circuit Breaker)**：连续失败就停止调用，避免雪崩
- **降级 (Fallback)**：主路径失败时走备用方案
- **隔离 (Bulkhead)**：限制单个故障的影响范围

### 10.4 日志 (Logging)

- **级别**：DEBUG / INFO / WARN / ERROR / FATAL
- **结构化日志**：JSON 格式，便于查询（不要 `print` 拼字符串）
- **关联 ID**：每个请求一个 trace_id，跨服务追踪
- **不要 log 敏感信息**：密码、token、PII
- **采样**：高频日志按比例抽样

### 10.5 指标 (Metrics)

- **类型**：Counter / Gauge / Histogram / Summary
- **RED 指标**：Rate / Errors / Duration（服务级）
- **USE 指标**：Utilization / Saturation / Errors（资源级）
- **黄金信号 (Google SRE)**：延迟、流量、错误、饱和度

### 10.6 链路追踪 (Tracing)

- **OpenTelemetry**：统一标准
- **概念**：Trace 由多个 Span 组成，跨服务传递 context
- **采样**：全采样代价高，按比例或按错误采样

### 10.7 健康检查

- **Liveness**：进程是否活着
- **Readiness**：是否准备好接受流量
- **Startup**：启动期专用（避免误判慢启动）

### 10.8 静默失败的危害

我们项目里的真实案例：`_write_file` 不尊重 `_cwd`，但因为 pytest 进程的 cwd 恰好是仓库根目录，单元测试一直"通过"——直到放进 sandbox 才暴露。教训：**测试要在真实使用场景下做，不能依赖偶然条件**。

📍 **项目对应**：训练 sandbox 用 git tracking 实现失败回滚；`AgentRunResult` 区分 `completed` / `error` / `budget_exceeded` 三种 stop_reason 用于诊断。

---

## 十一、安全

### 11.1 OWASP Top 10（Web）

1. **Broken Access Control**：权限控制失效
2. **Cryptographic Failures**：加密弱/不加密
3. **Injection**：SQL / NoSQL / Command / LDAP 注入
4. **Insecure Design**：架构层面的安全缺陷
5. **Security Misconfiguration**：默认密码、不必要的服务开启
6. **Vulnerable Components**：用了有漏洞的依赖
7. **Authentication Failures**：认证薄弱
8. **Software and Data Integrity Failures**：CI/CD 投毒、反序列化漏洞
9. **Logging and Monitoring Failures**：日志缺失，攻击无感知
10. **Server-Side Request Forgery (SSRF)**

### 11.2 通用原则

- **最小权限 (Principle of Least Privilege)**：默认拒绝，按需开放
- **纵深防御 (Defense in Depth)**：多层独立防御
- **安全默认 (Secure by Default)**：开箱即用就是安全配置
- **Zero Trust**：不信任任何网络位置，每次请求都验证
- **Fail Securely**：异常时进入安全状态而非开放状态

### 11.3 输入验证

- **白名单 > 黑名单**：列出允许的，而非禁止的
- **服务端必须验证**：客户端验证只是 UX，不能依赖
- **类型 + 范围 + 长度 + 格式** 四维校验
- **路径遍历**：用户输入拼路径前必须过滤 `..` `/` `\`
- **参数化查询**：杜绝 SQL 注入

### 11.4 认证 (AuthN) vs 授权 (AuthZ)

- **认证**：你是谁？（密码、令牌、生物特征）
- **授权**：你能做什么？（RBAC / ABAC）
- **会话管理**：Cookie / JWT / Session ID
- **MFA**：多因素验证

### 11.5 加密

- **传输加密**：TLS 1.2+
- **存储加密**：磁盘加密 + 应用层加密敏感字段
- **密码存储**：bcrypt / argon2 / scrypt（永远不要用 MD5/SHA1）
- **密钥管理**：KMS / HSM，不进代码不进日志
- **随机数**：用密码学安全的（`secrets` 模块，不是 `random`）

### 11.6 沙箱化

不可信代码执行的隔离层级（从弱到强）：
- 进程隔离
- chroot / namespace（Linux）
- 容器（Docker / runc）
- 微 VM（Firecracker、gVisor）
- 完整 VM（KVM、QEMU）

### 11.7 供应链安全

- **依赖锁定 + 完整性校验**（hash）
- **SBOM (Software Bill of Materials)**：列出所有组件
- **签名构建**：Sigstore、in-toto
- **CI/CD 加固**：限制密钥范围、审计 workflow 修改

### 11.8 命令注入防御

执行 shell 命令时永远不要拼字符串：
- 用 `subprocess.run([cmd, arg1, arg2])` 而非 `subprocess.run(f"cmd {arg}", shell=True)`
- 必须 shell=True 时用 `shlex.quote()` 转义

📍 **项目对应**：三层防御沙箱（macOS Seatbelt + bash 正则白名单 + git tracking）；`AgentPermissions` 默认 `allow_write=False`、`allow_shell=False`；web 端做了 `..` / `/` 路径遍历过滤。

---

## 十二、性能与资源管理

### 12.1 性能优化原则

1. **先测量，再优化**："Premature optimization is the root of all evil" (Knuth)
2. **优化热点**：80% 时间在 20% 代码（profiling 找出来）
3. **算法 > 微优化**：O(n²) 改 O(n log n) 远胜常数级优化
4. **缓存命中 > 计算更快**：多级缓存（CPU L1/L2/L3 → 内存 → SSD → 网络）

### 12.2 性能指标

- **延迟 (Latency)**：单次请求耗时（关注 p50/p95/p99，不是平均值）
- **吞吐 (Throughput)**：单位时间处理量
- **并发 (Concurrency)**：同时处理的请求数
- **资源利用率**：CPU / 内存 / IO / 网络

### 12.3 常见瓶颈

- **CPU**：算法低效、序列化开销
- **内存**：内存泄漏、GC 压力
- **IO**：磁盘随机读、跨数据中心调用
- **锁竞争**：粒度太粗、热点

### 12.4 优化技术

- **缓存**：减少重复计算/IO（注意失效策略）
  - LRU / LFU / TTL
  - 多级（本地 + Redis）
  - 缓存击穿 / 穿透 / 雪崩 防御
- **批处理**：合并多次操作（数据库批量插入、N+1 查询消除）
- **预取**：提前加载（CPU 分支预测、prefetch）
- **延迟计算 (Lazy)**：用到时再算
- **并行化**：多线程/多进程/SIMD
- **零拷贝**：减少内存拷贝（`sendfile`、`mmap`）
- **池化**：连接池、对象池、线程池

### 12.5 大语言模型场景特有

- **上下文管理**：token 是稀缺资源，要主动压缩
- **流式输出**：边生成边返回，降低首字延迟 (TTFT)
- **缓存 (Prompt Cache)**：固定前缀复用 KV cache，省钱省时
- **批处理 (Batching)**：多请求合并推理

### 12.6 资源生命周期

- **RAII (Resource Acquisition Is Initialization)**：构造时获取，析构时释放
- **Python 上下文管理器**：`with open(...) as f:` 自动关闭
- **Defer (Go) / try-finally**：保证清理一定执行
- **泄漏检测**：valgrind、tracemalloc、heap profiler

### 12.7 内存管理

- **栈 vs 堆**：栈快但有限，堆灵活但慢且需 GC
- **GC 类型**：标记清除 / 引用计数 / 分代 / 并发
- **避免大对象进老年代**：减少 full GC
- **对象池**：复用昂贵对象（数据库连接、线程）

📍 **项目对应**：`compact.py` 自动压缩接近 token 上限的上下文；`microcompact.py` 单工具结果按需截断；`RolloutRunner` 用 multiprocessing 池并行训练。

---

## 十三、构建与分发

### 13.1 构建系统

- **声明式 > 命令式**：描述"要什么"而非"怎么做"
- **可重复构建 (Reproducible Build)**：同样的输入产生 bit-by-bit 相同的输出
- **增量构建**：只重建变化部分
- **缓存**：本地 + 远程构建缓存（Bazel、Gradle、turborepo）

### 13.2 制品 (Artifact)

- **二进制可执行文件**
- **库**（动态/静态链接）
- **容器镜像**
- **包**（wheel/jar/npm tarball）
- **OS 包**（deb/rpm）

### 13.3 容器化

- **Docker 最佳实践**：
  - 多阶段构建（builder + runtime 分离，减小镜像）
  - 层缓存优化（变化频繁的放后面）
  - 非 root 用户运行
  - 最小基础镜像（distroless / alpine）
  - `.dockerignore` 排除无关文件
- **不变镜像**：每次部署用新镜像，不在容器里改

### 13.4 部署模式

- **蓝绿部署**：两份完整环境，切流量
- **金丝雀 (Canary)**：小流量先试
- **滚动更新**：逐个节点替换
- **特性开关**：代码部署但功能未开启
- **影子流量**：新版本接收复制流量但不返回

### 13.5 CI/CD

- **CI (Continuous Integration)**：每次 commit 自动构建测试
- **CD (Continuous Delivery/Deployment)**：自动到 staging / 生产
- **流水线阶段**：构建 → 单测 → 集成测 → 安全扫描 → 部署
- **必备**：分支保护、PR review、状态检查

### 13.6 包发布

- **Python**：`uv build` + `twine upload` → PyPI
- **Node**：`npm publish`
- **Rust**：`cargo publish` → crates.io
- **版本号必须递增**：注册中心通常不允许覆盖

📍 **项目对应**：`pyproject.toml` 配置 hatchling 构建后端；`[project.scripts]` 自动生成 `claw` 命令入口。

---

## 十四、版本控制与协作

### 14.1 Git 基础

- **三个区**：工作区 / 暂存区 / 仓库
- **常用命令**：`add` / `commit` / `push` / `pull` / `merge` / `rebase`
- **重写历史**：`commit --amend` / `rebase -i` / `cherry-pick`（仅本地分支用）
- **追溯**：`log` / `blame` / `bisect`（二分查找引入 bug 的提交）

### 14.2 分支策略

| 策略 | 特点 | 适用 |
|---|---|---|
| **Trunk-Based** | 都在 main，short-lived 分支 | 高频部署 |
| **Git Flow** | main / develop / feature / release / hotfix | 版本化发布 |
| **GitHub Flow** | main + feature branches | SaaS 持续部署 |
| **GitLab Flow** | + 环境分支（pre-production） | 多环境 |

### 14.3 提交信息规范

- **Conventional Commits**：`feat:` / `fix:` / `docs:` / `refactor:` / `test:`
- **结构**：标题（≤72 字符）+ 空行 + 正文（解释为什么，不是什么）
- **关联 issue**：`Closes #123`
- **原子提交**：一次提交一个独立变更（便于 revert）

### 14.4 Code Review

- **小 PR > 大 PR**：每次 ≤400 行最佳
- **focus**：正确性 / 可读性 / 测试 / 性能 / 安全
- **态度**：评论代码，不评论人；用问句而非命令句
- **自我 review**：提 PR 前自己先看一遍

### 14.5 协作工具

- **Pull Request / Merge Request**
- **Issue tracker**（GitHub Issues、Jira、Linear）
- **设计文档 (RFC / Design Doc)**：实现前先写
- **ADR (Architecture Decision Records)**：记录重大决策的背景和理由

### 14.6 冲突处理

- **合并冲突**：理解双方意图再解决，不要随便保留一边
- **强制推送 (`--force-push`)**：仅在自己的分支用，公共分支禁止
- **`--force-with-lease`**：比 `--force` 安全，避免覆盖他人提交

---

## 十五、代码质量与重构

### 15.1 可读性

- **命名**：揭示意图，避免缩写（`getUserById` > `gu`）
- **函数短小**：通常 ≤20 行；做一件事
- **避免深嵌套**：早返回 (early return) / 卫语句
- **注释解释 why 不解释 what**：what 看代码就知道
- **删掉死代码**：版本控制能找回，留着只会迷惑读者

### 15.2 代码异味 (Code Smell)

- **重复代码**：DRY
- **长函数 / 长类**：拆分
- **长参数列表**：参数对象
- **发散式变化 (Divergent Change)**：一个类因不同原因被改
- **散弹式修改 (Shotgun Surgery)**：一个变化要改多个地方
- **依恋情结 (Feature Envy)**：方法过多使用别的类的数据
- **数据泥团 (Data Clumps)**：几个字段总是一起出现 → 提取对象
- **基本类型偏执 (Primitive Obsession)**：用 string/int 表达领域概念 → 值对象
- **switch 语句**：考虑多态
- **平行继承**：每加一个 A 子类就要加一个 B 子类

### 15.3 重构手法 (Fowler 经典)

- **Extract Function / Variable**：提取
- **Inline Function / Variable**：内联
- **Rename**：改名
- **Move Function**：移到合适的类
- **Replace Conditional with Polymorphism**：条件改多态
- **Introduce Parameter Object**：参数对象
- **Replace Magic Number with Constant**：消灭魔法数字

### 15.4 静态分析

- **Linter**：Pylint / ESLint / Clippy
- **Formatter**：Black / Prettier / rustfmt（配合 pre-commit hook）
- **类型检查**：mypy / Pyright / TypeScript / Sorbet
- **复杂度度量**：圈复杂度 / 认知复杂度

### 15.5 技术债

- **故意债 vs 无意债**：故意走捷径上线 vs 不知情写错
- **管理**：识别 → 记录 → 优先级 → 偿还
- **永远还不完是正常的**：策略是控制总量，不是清零

---

## 十六、文档

### 16.1 文档分类（Diátaxis 框架）

| 类型 | 目的 | 例子 |
|---|---|---|
| **教程 (Tutorial)** | 学习导向 | "5 分钟入门" |
| **How-to 指南** | 任务导向 | "如何配置 SSO" |
| **参考 (Reference)** | 信息导向 | API 列表 |
| **解释 (Explanation)** | 理解导向 | 架构原理 |

### 16.2 README 必备内容

- 一句话项目介绍
- 安装步骤
- 快速开始示例
- 链接到详细文档
- License、贡献指南、行为准则

### 16.3 API 文档

- **从代码生成**：Sphinx / TypeDoc / rustdoc / godoc
- **OpenAPI / Swagger**：HTTP API 标准
- **示例代码必备**：每个端点至少一个 curl/SDK 示例

### 16.4 代码注释

- **公共 API 必须有 docstring**：参数、返回、抛出的异常
- **复杂逻辑解释 why**：非显而易见的决策
- **TODO / FIXME / HACK**：标记 + 关联 issue（不要永久 TODO）
- **注释要随代码更新**：过期注释比没注释更糟

### 16.5 变更记录

- **CHANGELOG.md**：用户视角的变更
- **遵循 Keep a Changelog 格式**：Added / Changed / Deprecated / Removed / Fixed / Security
- **从 commit 自动生成**：semantic-release / changesets

---

## 十七、机器学习工程特化

### 17.1 与传统软件的关键区别

- **代码 + 数据 + 模型** 三者都是制品
- **指标驱动**：accuracy/loss/reward 而非通过/失败
- **不确定性内生**：随机初始化、batch 顺序、浮点精度
- **数据是 1 等公民**：80% 工作在数据预处理 / 清洗 / 标注

### 17.2 可复现性

- **种子固定**：random / numpy / torch / cuda 全部
- **环境锁定**：conda env / docker
- **代码版本 + 数据版本 + 模型版本** 三元组追踪
- **超参记录**：每次实验配置都存档（W&B / MLflow）

### 17.3 数据工程

- **数据版本化**：DVC / Quilt / Delta Lake
- **数据质量**：完整性、一致性、时效性、准确性
- **数据契约 (Data Contract)**：上下游 schema 约定
- **数据漂移监控**：训练分布 vs 生产分布

### 17.4 训练流水线

- **数据加载**：DataLoader、shard、prefetch
- **训练循环**：forward / loss / backward / step
- **检查点**：定期保存，支持中断恢复
- **早停**：验证集不再提升就停

### 17.5 评估

- **多指标**：单一指标会被钻空子（Goodhart's Law）
- **离线评估 vs 在线评估**：离线代理指标，在线 A/B 测真实
- **基线 (Baseline)**：先跑最简单方法（随机/启发式）做对照
- **保留集 (Holdout)**：永远不能用来调参的最终测试集

### 17.6 强化学习特有

- **探索 vs 利用 (Exploration vs Exploitation)**：epsilon-greedy / UCB / Thompson sampling
- **奖励塑形 (Reward Shaping)**：稀疏奖励难学，密集奖励易作弊
- **离线 RL vs 在线 RL**：能否与环境交互
- **回放缓冲区 (Replay Buffer)**：打破时序相关性
- **Off-policy vs On-policy**：能否复用历史数据

### 17.7 LLM 工程

- **Prompt Engineering**：System / Few-shot / CoT / ReAct
- **工具调用 (Tool Use)**：模型输出结构化指令调用外部函数
- **上下文窗口管理**：token 预算、压缩、切片
- **缓存策略**：Prompt cache 节省成本
- **微调路径**：SFT → DPO/PPO → RLHF/RLAIF
- **评测**：自动指标 + LLM-as-judge + 人工评估

### 17.8 数据飞轮

```
用户使用 → 收集反馈/数据 → 过滤标注 → 训练改进 → 更好的产品 → 更多用户
```
关键：**数据采集质量**和**反馈闭环速度**决定飞轮转速。

### 17.9 模型部署

- **服务化**：TorchServe / Triton / vLLM / TGI
- **批处理 vs 流式**：吞吐 vs 延迟权衡
- **量化 (Quantization)**：FP16 / INT8 / INT4 减小模型
- **蒸馏 (Distillation)**：大模型教小模型
- **推测解码 (Speculative Decoding)**：小模型预测、大模型验证

📍 **项目对应**：`reviewer.py:combined_reward` 5 路加权奖励防 Goodhart；`SlimeDataAdapter` 实现 SFT 阈值过滤 + RL 全保留的飞轮数据；`DeterministicConfig` 固定 seed/temperature 保证复现。

---

## 附录 A：每章一句话总结

1. **架构**：边界划分要按变化频率
2. **模块化**：耦合最小、内聚最大
3. **API 设计**：难以误用比容易使用更重要
4. **配置**：分离、分层、安全默认
5. **依赖**：每个依赖都是债务
6. **CLI**：可管道、可脚本、可发现
7. **状态**：能不可变就不可变，能 append-only 就 append-only
8. **并发**：先选模型再写代码
9. **测试**：金字塔，假替身用最弱的够用就行
10. **错误**：fail-fast 但不静默吞
11. **安全**：最小权限 + 纵深防御
12. **性能**：先测量，再优化
13. **构建**：可重复 + 可缓存
14. **协作**：小 PR + 好提交信息
15. **代码质量**：可读性最重要
16. **文档**：四种类型各有目的
17. **ML 工程**：数据是 1 等公民，奖励别单一

---

## 附录 B：进阶阅读

- 《Clean Architecture》Robert C. Martin
- 《Designing Data-Intensive Applications》Martin Kleppmann
- 《Refactoring》Martin Fowler
- 《Site Reliability Engineering》Google
- 《Release It!》Michael Nygard
- 《The Pragmatic Programmer》Hunt & Thomas
- 《A Philosophy of Software Design》John Ousterhout
- 《Designing Machine Learning Systems》Chip Huyen
- 12factor.net - The Twelve-Factor App

---

*本文档持续更新。修订时优先扩充"通用知识点"，项目锚点保持精炼。*
