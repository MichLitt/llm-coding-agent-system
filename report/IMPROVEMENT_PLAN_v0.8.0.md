# Improvement Plan v0.8.0 — 多轮 Coding Agent 评测与高难仓库任务

**状态：** In progress — protocol/MCP/fixture implementation complete; frozen model baselines and SWE promotion replay remain  
**计划日期：** 2026-08-20  
**当前代码版本：** v0.7.4 + uncommitted v0.8.0 implementation worktree  
**当前 accepted baseline：** v0.7.2；SWE promoted lane 为 8 tasks / 5 repos，C3/C6 均为 2/8  
**计划目标：** 建立能区分“单轮修复能力”“跨轮需求保持能力”和“复杂仓库工程能力”的可复现评测体系，并用它驱动后续 Agent 行为改进。

> 本计划的首要原则是先固定评测协议和数据，再采集 baseline，最后才修改 Agent 行为。不得在同一个候选实验中同时更换任务集、指标定义、Prompt/Loop 和预算，否则结果不能归因。

## 0. 为什么现在做

当前项目已经具备多轮交互运行路径、持久化 run state、工具审计、验证门和单轮 benchmark runner，但正式质量证据仍主要来自：

- 单个自然语言任务对应一次 `agent.run(...)`；
- 40-task Custom suite，任务难度虽标记为 easy/medium/hard，但缺少可审计的复杂度维度；
- 8-task / 5-repo SWE promoted subset，适合小规模回归，却不足以稳定刻画复杂 repository repair 能力；
- task-level pass、steps、tokens、duration 等单轮汇总指标。

这会遗漏两类关键能力：

1. 用户追加、纠正或收紧需求后，Agent 是否正确继承上下文且不破坏已经通过的行为；
2. 面对跨模块、迁移、并发、恢复和兼容性约束时，Agent 是否能完成真正接近工程工作的修改。

因此 v0.8.0 不把“多轮”理解为增加内部 ReAct step，也不把“更难”理解为继续给现有任务贴 `hard` 标签。它要新增两个彼此独立、可分别归因的评测 lane：

- **ConversationBench v1**：考察多个外部用户回合构成的一次持续 coding session；
- **ComplexCodeBench v1**：考察单个明确需求下的复杂、多约束 repository task。

## 1. 版本边界

### 1.1 v0.8.0 必做

- 定义并实现多轮任务、回合结果、会话结果和复杂度画像的版本化 schema；
- 实现默认关闭、stdio-first 的 MCP 工具适配，并保留每次发现与调用的审计 artifact；
- 在同一 workspace、同一 `AgentSession` 中执行多个用户回合；
- 增加 per-turn verification、约束保持检查、会话级 artifact 和指标；
- 建立 6 个多轮 dev tasks、12 个冻结 multi-turn test tasks；
- 建立 6–8 个确定性的本地复杂任务；
- 将 SWE promoted subset 从 8 个任务扩大到 12 个任务，覆盖至少 6 个 upstream repos；候选快照不能替代 promotion replay evidence；
- 在不修改 Agent 推理行为的前提下，采集第一版 baseline 和失败分类。

### 1.2 后续 candidate cycle 才做

- 根据 baseline 的主失败类型修改 context、memory、planning、verification recovery 或 prompt；
- 使用完全相同的 frozen test split、模型、参数和预算做 baseline/candidate 对比；
- 形成新的 improvement report 和 accepted baseline。

行为候选版本可以命名为 v0.8.1，或在实现前另行确定；不得把尚未观察到的失败模式预设为 runtime 方案。

### 1.3 非目标

- 多 Agent 协作；
- IDE 插件或重前端；
- 全量 SWE-bench leaderboard 宣称；
- 远程 MCP transport、OAuth/动态凭证委派或未经 allowlist 的 server 自动发现；
- 以 LLM-as-a-judge 取代可执行验证；
- 在 test split 上反复调 Prompt；
- 对多轮会话做 mid-turn 或 mid-conversation 恢复。v1 只允许在 conversation task 边界 resume。

## 2. 评测对象与术语

必须在代码、artifact 和报告中严格区分：

| 名称 | 定义 | 当前对应物 |
|---|---|---|
| Agent step | 一次内部推理/工具执行循环 | `TurnResult.steps` 内部计数 |
| User turn | 用户在持续会话中发送的一条新消息 | v0.8.0 新增 |
| Conversation task | 共享同一初始仓库、workspace 和 session 的 3–5 个 user turns | v0.8.0 新增 |
| Phase check | 某个 user turn 完成后必须满足的局部检查 | v0.8.0 新增 |
| Final check | 整个 conversation/task 完成后必须满足的验收检查 | 现有 verification 的扩展 |
| Retained constraint | 在后续回合仍然有效、不得被覆盖的既有要求 | v0.8.0 新增 |

CLI 输出和报告不得用裸 `turn` 同时表示内部 Agent step 与外部 user turn。

## 3. ConversationBench v1 设计

### 3.1 数据模型

建议新增独立模型，避免向现有 `TaskSpec` 塞入可选嵌套字段：

```python
@dataclass
class ConversationTurnSpec:
    turn_id: str
    user_message: str
    intent: str
    max_steps: int = 15
    phase_checks: list[dict[str, Any]] = field(default_factory=list)
    introduces_constraints: list[str] = field(default_factory=list)
    retains_constraints: list[str] = field(default_factory=list)
    expects_clarification: bool = False

@dataclass
class ConversationTaskSpec:
    conversation_id: str
    split: str                  # dev | test
    category: str
    difficulty: str
    setup_files: list[str]
    turns: list[ConversationTurnSpec]
    final_checks: list[dict[str, Any]]
    verification_contract: dict[str, Any]
    max_total_steps: int
    metadata: dict[str, Any]
```

约束必须使用稳定 ID，例如 `preserve_sync_api`，而不是只保存自然语言。每个 constraint ID 必须能映射到至少一个确定性 check；无法自动验证的软约束只能作为 supplementary observation，不能决定主 pass/fail。

### 3.2 执行语义

每个 conversation task 按以下顺序执行：

1. 创建并初始化一次 task workspace；
2. 创建一个 Agent，并包装为同一个 `AgentSession`；
3. 按 manifest 顺序逐条发送 user message；
4. 每个回合结束后执行该回合 `phase_checks`，记录 workspace diff 和已有约束状态；
5. 后续回合不得 reset Agent，也不得重新创建 workspace；
6. 最后执行 `final_checks`，生成 conversation-level 结果；
7. task 完成后才允许 checkpoint 和 resume；失败会话从初始 fixture 重跑，不从中间回合恢复。

不得把多条 user message 拼接成一个 prompt。这样只能测长指令遵循，不能测跨轮状态保持。

`AgentSession.send()` 可以增加向后兼容的可选执行参数，以便每个 user turn 设置 `max_steps` 和 verification hook；无参数调用必须保持 v0.7.4 行为。

### 3.3 首版任务构成

首版共 18 个任务，每个 3–5 个 user turns：

| 类别 | Dev | Frozen test | 主要能力 |
|---|---:|---:|---|
| clarification-before-action | 1 | 2 | 信息不足时先澄清，澄清前不修改 workspace |
| incremental-requirement | 1 | 2 | 在已有实现上增加需求并保留旧行为 |
| user-correction | 1 | 2 | 接受纠正、废弃冲突假设并完成迁移 |
| regression-preservation | 1 | 2 | 新需求通过且先前 checks 继续通过 |
| contextual-reference | 1 | 2 | 正确解析“之前的接口/限制”等上下文指代 |
| scope-and-constraint-control | 1 | 2 | 遵守不可改测试、兼容性或文件范围约束 |
| **合计** | **6** | **12** | — |

任务不应只是把一个单轮需求机械拆成三句。每个后续回合至少引入一种只有读取会话历史和当前 workspace 才能正确处理的增量信息。

### 3.4 Fixture 原则

- Dev 与 test 使用不同 fixture、标识符、需求文本和验证文件，禁止仅重命名复制；
- test split 一经 baseline 前冻结，不再因 candidate 表现改题；
- hidden checks 与任务文本分离，Agent 不得读取 evaluator-only 路径；
- 每个任务至少包含一个 retained constraint；
- correction 类任务必须明确哪个旧要求被替代、哪些旧要求仍保留；
- clarification 类任务第一回合的硬门槛是 workspace diff 为空，且回复满足可检查的澄清意图；
- 所有 setup、phase checks 和 final checks 在干净环境中连续运行 3 次均应得到一致结果。

## 4. 多轮指标

### 4.1 主指标

| 指标 | 定义 | 用途 |
|---|---|---|
| Conversation Success Rate | 所有 final checks 通过，且所有 hard constraints 在适用回合均保持的会话比例 | 会话级主质量指标 |
| Turn Pass Rate | 通过对应 phase checks 的 user turns / 有 phase checks 的 user turns | 定位失败发生阶段 |
| Constraint Retention Rate | 在适用后续回合中仍满足的 retained constraints / 应保持的 constraints | 衡量遗忘和覆盖 |
| Regression-Free Follow-up Rate | 已通过前一阶段后，新增需求完成且此前 checks 仍通过的 follow-up 比例 | 衡量增量修改安全性 |
| Correction Recovery Rate | correction turns 中正确替换冲突要求并通过新旧非冲突 checks 的比例 | 衡量纠错能力 |

### 4.2 诊断指标

| 指标 | 定义 |
|---|---|
| Context Reference Accuracy | contextual-reference 回合中，被引用对象对应 checks 通过的比例 |
| Premature Action Rate | `expects_clarification=true` 的回合中，澄清前产生 workspace 修改的比例；越低越好 |
| Conversation Completion Depth | 每个会话在第一次 hard failure 前完成的有效 user turns / 总 user turns |
| Follow-up Efficiency | follow-up 回合的 steps、tool calls、tokens、latency；分别报告均值与 p50/p95，不合并成不透明总分 |
| Rework Ratio | 后续回合撤销或反复修改此前已正确文件的 edit events / follow-up edit events |

### 4.3 指标边界

- pass/fail 以可执行 checks、workspace diff 和结构化事件为主；
- LLM judge 只能用于回复清晰度等 supplementary 指标，必须先在人工标注小样本上校准；
- 不创建未经解释的加权综合分；
- 分母为 0 时输出 `null` 与原因，不输出 0 冒充失败；
- 所有 aggregate 必须同时报告 numerator、denominator、task IDs 和 seed；
- Conversation Success Rate 是首版 promotion primary metric，其定义必须在 test baseline 前冻结。

## 5. Conversation artifact 合同

每个 user turn 至少保存：

```json
{
  "schema_version": "conversation-eval/v1",
  "conversation_id": "conv_test_001",
  "turn_id": "t2",
  "user_message_sha256": "...",
  "workspace_before_sha256": "...",
  "workspace_after_sha256": "...",
  "phase_checks": {"passed": 3, "total": 3},
  "constraint_results": {"preserve_sync_api": true},
  "steps": 8,
  "tool_calls": 11,
  "tokens": 6200,
  "duration_seconds": 41.2,
  "agent_final_status": "success",
  "termination_reason": null,
  "failure_categories": []
}
```

会话级 artifact 还必须包含：

- task manifest SHA256、fixture tree SHA256、代码 commit、模型 profile 和模型名；
- preset、完整有效 config、model seed、预算和 timeout；
- turn results 的有序引用；
- final checks、conversation success、指标 numerator/denominator；
- workspace 最终 diff SHA256；
- infra/model/agent/verification 分层 failure taxonomy。

首版可以把聚合后的扁平指标作为可选 extras 上报现有 `agent/v1`；raw per-turn artifact 是事实源。只有 EvalOps 必须查询嵌套回合数据时，才协调新增版本化跨仓 contract。

## 6. ComplexCodeBench v1 设计

### 6.1 为什么独立于现有 Custom suite

现有 Custom suite 适合作为便宜、快速的日常 regression lane，应保持稳定。复杂任务另建 benchmark，避免扩大 fixture 后导致历史 40-task 指标失去可比性。

每个复杂任务必须带 `complexity_profile`：

```yaml
complexity_profile:
  subsystems: [api, persistence, worker]
  requirement_count: 5
  expected_files_touched_min: 3
  cross_module: true
  persistence_migration: true
  async_or_concurrency: false
  backward_compatibility: true
  recovery_semantics: true
  verification_layers: [unit, integration, migration]
  setup_class: local_deterministic
```

`expected_files_touched_min` 只用于数据集复杂度审计，不作为要求 Agent 凑修改文件数的 pass condition。

### 6.2 本地确定性 lane

首版选择 6–8 个任务，目标为 8 个：

| Task family | 核心难点 | 必须验证 |
|---|---|---|
| Cross-module API refactor | 调用链、类型和兼容入口同步修改 | unit + integration + old API compatibility |
| SQLite migration | fresh init 与 previous-schema upgrade | schema/data preservation + idempotent startup |
| Async worker retry/idempotency | lease、重试、重复提交 | retry limit + duplicate suppression + terminal state |
| Concurrency race repair | 锁粒度、竞争和确定性重放 | repeated concurrency test + no deadlock |
| Config upgrade | 默认值、旧配置和错误输入 | old/new config + validation failures |
| Plugin lifecycle | 注册、加载、失败隔离和卸载 | lifecycle integration + partial failure cleanup |
| Resource cleanup/recovery | timeout、异常和取消路径 | handles/processes cleaned + retry succeeds |
| Cross-module regression | 新功能与既有行为同时保持 | focused tests + full fixture suite |

每个任务应满足：

- 至少 2 个逻辑子系统、3 个独立要求、2 层 verification；
- fixture 内至少有 4 个实现文件和一组现有测试；
- 至少一个 failure-path 或 compatibility requirement；
- 推荐 `max_steps=25–40`，但正式预算在 dev pilot 后、test baseline 前统一冻结；
- evaluator hidden checks 不允许被 Agent 修改；
- gold patch 在干净环境连续回放 3 次通过；原始 buggy state 必须稳定失败至少一个 fail-to-pass check。

### 6.3 SWE promoted 扩容

按 8 → 12 → 16 的两级策略扩容，本周期只承诺到 12：

1. 在查看目标模型结果前，用固定 selection rubric 选择 4 个新任务；
2. 总覆盖从 5 个 repo 提升到至少 6 个 repo；
3. 每个候选任务执行 pinned checkout、gold patch replay、fail-to-pass 和 pass-to-pass 验证；
4. setup/test command 在 clean environment 连续 3 次一致；
5. 因依赖失效、网络、平台差异或不可固定服务导致的不稳定任务不得进入 promoted manifest；
6. 12-task lane 稳定完成 baseline/candidate 后，才能单独提案扩到 16。

任务选择 rubric 至少记录：repo、问题类型、patch 涉及子系统、测试层级、环境成本、历史 flakiness、是否需要网络、gold replay 结果。不得依据某个 preset 容易通过来选题。

## 7. 复杂任务指标

复杂任务继续以 verification pass 为主，但增加分层统计：

- task pass rate 与 strict success rate；
- fail-to-pass checks passed、pass-to-pass regression preservation；
- partial verification credit；
- 按 `cross_module`、migration、concurrency、compatibility、recovery 等复杂度维度分层的 pass rate；
- steps、tokens、wall time、tool failures 和 retry cost；
- patch footprint：修改文件数、测试文件修改、越界路径修改；
- infra failure rate 单独报告，不混入能力失败，也不得静默剔除。

由于首版样本量小，报告必须给出原始计数和逐任务结果，不用小样本百分比制造统计显著性结论。

## 8. 数据冻结与实验协议

### 8.1 Split 与泄漏控制

- ConversationBench：6 dev + 12 frozen test；
- Complex local：建议 2 dev + 6 frozen test，若最终为 8 tasks；
- SWE：smoke 用于 harness，promoted 12-task 用于正式对比；
- Prompt、memory 和 runtime 调优只看 dev 与 baseline failure taxonomy；
- test 任务只在预先登记的正式 baseline/candidate run 执行；
- manifest 记录 task IDs、文件 hash、fixture tree hash、checks hash 和 schema version。

### 8.2 Formal run 控制变量

Baseline 与 candidate 必须保持：

- 同一 frozen manifests 和顺序；
- 同一代码起点/fixture、模型、temperature、top-p、tool schema 和环境；
- 同一 per-turn/per-task steps、token、timeout 和安装预算；
- 同一 seeds；
- 同一 verification 和失败分类版本。

Conversation frozen test 在协议稳定后执行 3 seeds。Complex local 可执行 3 seeds。SWE 12-task 先做 1-seed operational baseline；只有环境与成本稳定后再做 3-seed formal compare。

### 8.4 MCP 工具适配协议

MCP 是 Agent 的受控工具扩展，不是另一个 Agent 或不受约束的插件系统。首版只支持由显式配置启动的本地 stdio server：

- `mcp_servers` 配置必须列出 server ID、可执行命令、参数、工作目录、工具 allowlist、每次调用 timeout 和环境变量 allowlist；默认配置为空，现有 C3/C6 与 G3 基线不注册任何 MCP 工具。
- 适配层把已发现的 MCP tool 映射到既有 Agent tool contract，使用稳定的 `mcp.<server_id>.<tool_name>` 命名空间，并在名称冲突、无效 JSON Schema 或 server handshake 失败时拒绝启动而非静默覆盖。
- 每次 run 保存 server 配置 SHA256、可执行文件/version fingerprint、discovered tool schema hash、调用参数摘要 hash、结果状态、duration、timeout/cancel 与 failure category；原始 secrets、完整敏感参数和任意 server 输出不得进入 artifact。
- server 进程必须由 run 生命周期拥有：启动失败、tool timeout、Agent cancellation 和 run completion 都要关闭子进程及其管道。MCP server 不继承未列入 allowlist 的环境变量。
- 首版不支持网络 transport、自动安装 server、运行时增删工具或直接把 MCP server 暴露给用户；这些能力需在独立安全评审后再提案。

MCP 评测先使用仓库内 deterministic fake server，不调用真实外部服务。最小验证覆盖 tool discovery、success、structured error、malformed response、schema/name collision、timeout、cancel 和 cleanup。待适配层稳定后，才新增一个冻结的 MCP-assisted task lane；它与现有 Custom、Conversation、Complex 和 SWE lanes 分开报告，不得用 MCP 结果重写既有基线。

### 8.3 阈值冻结

当前计划不凭空设定绝对提升百分比。正确顺序是：

1. 在 dev split 上完成 harness pilot；
2. 冻结 metric definitions、test manifests 和预算；
3. 运行 baseline；
4. 根据 baseline 与产品目标，**在实现 candidate 前**登记 primary metric、最小有意义改善和 non-regression gates；
5. 实现 candidate；
6. 一次性运行 frozen comparison，保留所有 seeds 和负面结果。

最低 non-regression gate 应包含：现有 40-task Custom lane 不下降、SWE promoted 不下降、ConversationBench hard constraint 不恶化；具体容忍度在 baseline 后预注册。

## 9. 分阶段实施计划

### Phase A0 — MCP 适配基础与隔离测试（2–4 个工作日）

工作项：

- 定义 `MCPServerConfig`、discovery record、tool-call record 和 artifact schema；
- 实现 stdio client lifecycle、tool discovery、命名空间映射、输入验证、timeout/cancel 与 cleanup；
- 新增 deterministic fake MCP server 和 success/failure/collision/timeout/cancel 测试；
- 确认 `mcp_servers` 缺省时现有 tool set、CLI、Custom/SWE/HumanEval artifact 与 v0.7.4 行为完全一致。

完成信号：

- 不启动真实外部 server 即可覆盖完整 lifecycle；
- 子进程和管道在失败、超时与取消后无泄漏；
- 默认关闭配置下，既有 benchmark 的 tool schema 与行为快照不变；
- 每项 MCP tool invocation 都能在 artifact 中关联到固定 server/tool schema fingerprint。

### Phase A — 协议与离线 runner（3–5 个工作日）

工作项：

- 新增 conversation schema、loader、runner、results 和 metrics；
- 同一 session 顺序执行 user turns，并在每回合运行 phase checks；
- 保存 turn-level JSONL、conversation summary 和 manifest；
- 支持 conversation task 边界 checkpoint/resume；
- 用 deterministic fake agent 覆盖成功、遗忘、纠正失败、澄清前修改、异常和 resume 场景。

完成信号：

- 不调用真实 LLM 也能完整重放全部 runner 状态；
- 重复运行得到相同 task/fixture/check hashes；
- 中途失败不会生成伪 completed conversation；
- 现有 `TaskSpec`、Custom/SWE/HumanEval 行为与 artifact 保持兼容。

### Phase B — ConversationBench v1 数据（4–6 个工作日）

工作项：

- 先完成 6 个 dev tasks，进行协议和可判定性评审；
- 再独立编写 12 个 frozen test tasks；
- 为每个 constraint 建立机器可执行映射；
- 对 fixture 与 checks 做 3 次 clean replay；
- 冻结 manifests，并记录 author/reviewer、hash 和冻结时间。

完成信号：

- 六类任务均有覆盖，且每个 test task 有至少一个 retained constraint；
- 无 dev/test fixture 复制或 hidden-check 泄漏；
- 人工抽查能够从 artifact 解释每个指标分子和分母。

### Phase C — ComplexCodeBench v1 与 SWE-12（5–8 个工作日）

工作项：

- 建立 6–8 个 complex local fixtures 和 `complexity_profile`；
- 将现有 8-task SWE promoted manifest 扩到 12 tasks / ≥6 repos；
- gold-patch replay 与 clean-environment flakiness audit；
- 增加复杂度分层 analysis output。

完成信号：

- 所有 local buggy fixtures 稳定失败、gold patches 稳定通过；
- SWE-12 的 12 个任务均完成 attribution audit；
- infra failure 可以从 Agent capability failure 中单独识别。

### Phase D — 无行为变化的 baseline（3–5 个工作日，另计模型运行时间）

工作项：

- 冻结代码和所有 manifests；
- Conversation test 与 Complex local 运行 3 seeds；
- SWE-12 先运行 1 seed，稳定后决定是否执行 3 seeds；
- 生成逐任务结果、aggregate、failure taxonomy 和成本摘要；
- 写 `BASELINE_0_8_0.md`，明确限制和负面案例。

完成信号：

- artifacts 能从 raw turn/task results 重算；
- 没有 selective retry 或只保留成功 seed；
- baseline 报告不宣称 Agent 行为已提升。

### Phase E — 失败驱动的 runtime candidate（5–10 个工作日，单独版本）

工作项：

- 只选择 baseline 中占比最高且可干预的 1–2 个 failure classes；
- 在 dev split 上实现最小 runtime 改进；
- 预注册 frozen comparison gates；
- 运行同任务、同模型、同预算 candidate；
- 接受则写 improvement report 和新 baseline，拒绝则保留 negative artifact。

完成信号：

- 改动与目标 failure class 有明确机制对应；
- primary metric 达到预注册门槛，所有 required non-regression gates 通过；
- 结果可进入 EvalOps compare/gate，不把 operational promotion 等同于统计显著性。

## 10. 建议文件范围

预计新增：

- `coder_agent/eval/conversation_models.py`
- `coder_agent/eval/conversation_runner.py`
- `coder_agent/eval/conversation_metrics.py`
- `coder_agent/eval/benchmarks/conversation/`
- `coder_agent/eval/benchmarks/complex_code/`
- `tests/test_conversation_runner.py`
- `tests/test_conversation_benchmark.py`
- `tests/test_complex_code_benchmark.py`

预计修改：

- `coder_agent/core/session.py`：仅增加向后兼容的 turn execution options；
- `coder_agent/cli/eval.py`：注册新 benchmark 与 CLI 参数；
- `coder_agent/eval/eval_checkpoint.py`：新增版本化 conversation checkpoint，不改变旧格式语义；
- `coder_agent/eval/analysis*.py`：增加会话与复杂度分层输出；
- SWE generated manifest、source snapshot 和 overrides；
- `README.md`、新 baseline/improvement reports。

实现时如需改变 `agent/v1` 或其他跨仓 schema，必须拆为协调 PR，补 producer/consumer contract tests 和 root closure；首版优先避免此依赖。

## 11. PR 切片与依赖

| PR | Owner | 内容 | 依赖 | 预计审阅规模 |
|---|---|---|---|---|
| PR-0 | Agent | MCP schema、stdio adapter、fake server 与 lifecycle tests | 无 | 1–3 天 |
| PR-1 | Agent | Conversation schema + deterministic runner + artifacts | PR-0 的 tool artifact contract | 1–3 天 |
| PR-2 | Agent | Conversation dev/test fixtures + metrics | PR-1 | 1–3 天 |
| PR-3 | Agent | Complex local benchmark + complexity analysis | 无，可与 PR-2 顺序独立 | 1–3 天 |
| PR-4 | Agent | SWE promoted 8→12 + attribution audit | PR-3 的 rubric | 1–3 天 |
| PR-5 | Agent | Frozen baseline artifacts + report | PR-1–4；MCP lane 仅在 PR-0 稳定后纳入 | 实验 PR |
| PR-6 | Agent | Failure-driven runtime candidate | PR-5 | 单独行为版本 |
| PR-7（可选） | EvalOps + Agent | 嵌套 turn/MCP ingestion contract | 只有 UI/query 需要时 | 协调 PR |

每个 PR 必须独立可测试；数据 PR 与 runtime behavior PR 不得合并成一个不可审计 diff。

## 12. 验证矩阵

| 变更 | Targeted tests | Owner gate | Evidence gate |
|---|---|---|---|
| Conversation schema/runner | fake-agent success/failure/reset/resume/hash tests | `uv run pytest` | deterministic fixture replay |
| MCP adapter | fake-server discovery/call/schema-collision/timeout/cancel/cleanup tests | `uv run pytest` | default-off tool-schema snapshot + lifecycle artifact audit |
| Conversation data | loader、constraint mapping、hidden path tests | `uv run pytest` | 3× clean replay + manifest hash |
| Complex local data | buggy/gold、migration、concurrency/recovery tests | `uv run pytest` | 3× clean replay |
| SWE-12 | loader、manifest、gold replay、test overlay tests | `uv run pytest` | attribution audit artifact |
| Baseline | artifact recomputation tests | owner suite + `git diff --check` | fixed tasks/seeds/model/config report |
| Runtime candidate | focused failure regressions + full owner suite | affected root gates | frozen baseline/candidate compare |

文档和计划阶段不运行真实模型；真实模型执行必须在 manifests、预算和 baseline command 已冻结后进行。

## 13. 风险与停止条件

### 风险 A：把脚本化多回合误当成真实交互

缓解：任务必须包含依赖历史的追加、纠正或指代；报告明确它是 controlled scripted conversation benchmark，不外推到开放式用户研究。

### 风险 B：指标过多导致挑数字

缓解：baseline 前冻结一个 primary metric、required non-regression metrics 和 diagnostics；报告所有预注册指标。

### 风险 C：新任务被 Agent 看到 hidden checks

缓解：evaluator-only 文件置于 workspace 边界外，通过 harness 注入执行；增加路径隔离测试。

### 风险 D：SWE 扩容重新引入环境噪声

缓解：gold replay、3× clean run、无网络优先和 layered infra taxonomy；不能稳定归因的任务不进入 promoted lane。

### 风险 E：成本膨胀

缓解：dev → frozen local → SWE 1 seed → formal 3 seeds 逐级放行；每级先统计 tokens、wall time 和 timeout，再决定下一层。

遇到以下情况必须停止并重新评审协议：

- 同一 fixture/check 在 clean replay 中结果不一致；
- test split 或 hidden checks 泄漏给 Agent；
- baseline 后仍需要改变 primary metric 定义；
- candidate 必须扩大预算才能产生提升；
- infra failures 无法与能力 failures 分离；
- 跨仓 contract 不能向后兼容且尚无协调实施计划。

## 14. v0.8.0 Definition of Done

v0.8.0 只有同时满足以下条件才能完成：

- [ ] ConversationBench v1 schema、runner、artifact 和 CLI 可用；
- [ ] MCP stdio adapter、默认关闭安全边界、fake-server lifecycle tests 和调用审计 artifact 可用；
- [ ] 6 dev + 12 frozen test conversation tasks 完成审计与 hash freeze；
- [ ] 6–8 个 ComplexCodeBench local tasks 完成 buggy/gold replay；
- [ ] SWE promoted 扩到 12 tasks / 至少 6 repos；
- [ ] 所有新增 runner/data tests 与完整 owner suite 通过；
- [ ] Conversation/Complex/SWE baseline 按固定模型、预算和 seeds 运行并保留全部结果；
- [ ] `BASELINE_0_8_0.md` 能从 raw artifacts 重算指标；
- [ ] README 清楚区分 capability、baseline 和尚未证明的 improvement；
- [ ] 未修改 Agent 行为，或行为修改已被拆到独立 candidate version 并重新基线。

## 15. 阶段性交付价值

完成 Phase A–C，可以诚实描述“设计并实现了多轮 coding-agent evaluation framework 与复杂 repository-task suite”，但不能声称质量提升。

完成 Phase D，可以展示：

- 多轮需求保持和纠错的细粒度指标；
- migration/concurrency/recovery 等工程任务的分层能力图谱；
- 从 raw artifacts 到 baseline report 的可复现证据链。

只有 Phase E 通过预注册 gate 后，才可以在简历中写具体改善数字。推荐表达结构为：

> Designed a versioned multi-turn repository-task benchmark with per-turn verification, constraint-retention and regression metrics, then used its frozen failure taxonomy to improve **[primary metric]** from **X** to **Y** across **N tasks / S seeds** under fixed model and execution budgets.

这里的 X、Y、N、S 必须来自 accepted artifact，不在计划阶段填写。
