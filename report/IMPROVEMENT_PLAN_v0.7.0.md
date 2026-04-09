# Improvement Plan v0.7.0 — 编辑原语、行为约束与高信号评测

**Date:** 2026-04-08  
**Branch:** (TBD)  
**Baseline:** v0.6.0 accepted baseline — Custom `C3=90.0%`, `C4=87.5%`, `C6=85.0%`; SWE smoke `1/1`, SWE promoted compare `0/3`

> **Version naming assumption:** 本计划使用 `v0.7.0` 作为后续版本标签，而不是 `v0.6.1`。原因是计划范围会触及 tool behavior、agent loop policy、prompt guidance、benchmark/rebaseline 契约，属于需要重新建立能力基线的版本级变更，而不是单点补丁。

> **Document status:** 本文档在被合并回主仓、进入主 `report/` 目录并被当前路线文档或 active playbook 引用之前，只应视为执行草案，而不是唯一的统一执行依据。

---

## 0. 背景与动机

`0.6.0` 已经把 runtime contract、per-run workspace、strict resume contract、official SWE-bench Lite subset 接入和 task-local env hardening 做到了一个可接受的工程基线。当前项目的主要矛盾不再是“评测是否可信”，而是：

- agent 在真实 repo-repair 场景里的成功率仍然偏低，尤其是 SWE promoted subset 仍为 `0/3`
- 编辑原语仍过于粗糙，复杂 patch 任务常被整文件重写或单次字符串替换限制住
- agent 在失败恢复时仍会出现测试编辑漂移、ad hoc 安装依赖、过早总结等行为问题
- 当前 benchmark 结构能发现大方向问题，但对“能力真的提升了什么”仍不够高信号
- 版本、文档、配置注释之间存在轻微漂移，影响外部理解和后续维护

因此 `v0.7.0` 不应继续以 runtime plumbing 为主线，而应转向三个更直接影响 agent 上限的方向：

1. **更强的编辑工具**
2. **更硬的行为约束**
3. **更高信号的 benchmark 与分析闭环**

---

## 1. 版本目标

### 1.1 主要目标

- 把 agent 的核心编辑能力从“能写文件”提升到“能稳定做多处、局部、可验证的补丁修改”
- 降低失败恢复过程中的无效行为，尤其是测试漂移、重复安装、过早停止
- 在不牺牲 artifact 审计性的前提下，提高 benchmark 对真实 repo-repair 能力的判别力

### 1.2 次要目标

- 重新确认 `C4` memory lane 在 `0.6.0` runtime 上是否仍值得保留为 promoted candidate
- 清理版本与文档语义漂移，减少“代码到 0.6，包版本还在 0.4.x”这类理解成本

### 1.3 非目标

以下内容不进入 `v0.7.0` 主线：

- HTTP API / job manager / cancellation 体系
- 多 agent 并行协作框架
- GUI / web frontend
- 面向公开 leaderboard 的 SWE-bench 全量宣称

---

## 2. 范围与阶段拆分

- **v0.7.0a（必做）**：编辑原语升级
  - 新增 patch-style 文件编辑工具
  - 保留 `write_file` 用于整文件写入，但不再承担复杂局部 patch 主路径
  - 增强编辑失败反馈，减少 `old_text not found` 模糊失败

- **v0.7.0b（必做）**：agent 行为约束
  - 强化失败恢复期的实现优先策略
  - 控制测试编辑、无界安装依赖、过早完成
  - 对 verification failure 注入更具体、更可执行的反馈

- **v0.7.0c（必做）**：benchmark 与分析升级
  - 扩大 SWE smoke / promoted subset
  - 增加 failure taxonomy 和 infra-vs-agent 分层分析
  - 重新验证 `C4 similarity` 是否仍值得 promotion

- **v0.7.0d（应做）**：版本与文档契约整理
  - 对齐 package version、README、report、config 注释
  - 明确“package version”与“accepted baseline cycle”之间的关系

### 2.1 实施前必须先定清的四个契约

在进入具体版本实现前，以下四个契约必须先被写实，否则本计划仍会停留在方向性文档，而不是可执行计划：

1. **重试状态拆分契约**
   - 当前恢复状态不能再只靠 `awaiting_retry_verification` 这个单一布尔位表达
   - 必须显式区分：
     - 普通工具失败后的重试窗口
     - verification failure 后的恢复窗口

2. **安装预算拦截点契约**
   - “超预算不执行命令”不能只写在 loop 层描述里
   - 需要明确落在 pre-dispatch 或 `RunCommandTool` 内部的哪一层，以及对应的 task-scoped 计数如何 reset

3. **`patch_file` 事务语义契约**
   - 既然要求顺序应用多个 edit，又要求失败时不留下半成功写盘状态，就必须明确“一次内存应用、一次最终写回”的事务语义

4. **分层分析出口契约**
   - “infra-vs-agent 分层报告”不能只新增 taxonomy category 名称
   - 需要明确它在 manifest metadata、analysis API、CLI 输出、comparison report 中分别从哪里进入、如何展示

以下各 Change 都以这四个契约已经明确为前提。

---

## 3. 详细改进项

### Change 1：新增 `patch_file` 工具，建立局部补丁主路径

#### 问题

当前文件编辑工具只有两种模式：

- `write_file(operation="write")`：整文件覆盖
- `write_file(operation="edit")`：单次 `old_text -> new_text`

这对简单题足够，但对 repo-repair 场景不够：

- 一个文件需要多处修改时，模型容易退化成整文件重写
- 单次替换命中失败时，错误信息过粗，恢复成本高
- 无法显式表达“这次 patch 应当修改 2 处、每处必须精确命中”的意图

#### 设计

新增独立工具 `patch_file`，而不是继续扩展 `write_file` 的语义。

建议 schema：

```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string"},
    "edits": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "old_text": {"type": "string"},
          "new_text": {"type": "string"},
          "expected_replacements": {"type": "integer", "default": 1}
        },
        "required": ["old_text", "new_text"]
      }
    }
  },
  "required": ["path", "edits"]
}
```

行为要求：

- 顺序执行 `edits`
- **事务语义明确化**：先读入原文件内容，在内存副本上顺序应用所有 `edits`；仅当所有 edit 都通过校验后，才一次性写回磁盘
- 每个 edit 在执行前先统计匹配次数
- 当 `actual_matches != expected_replacements` 时立即失败，并返回明确统计信息
- 任一 edit 失败时，磁盘上的原文件保持不变；返回值中仍应包含已校验过的 edit 统计，方便模型恢复
- 返回结果中包含：
  - 修改的文件
  - 成功应用的 edit 数量
  - 每个 edit 的匹配次数
- 不支持路径逃逸
- 不支持对不存在文件静默创建

#### 文件范围

- `coder_agent/tools/file_tools.py`
- `coder_agent/core/tool_registry.py`
- `coder_agent/core/agent_prompt.py`
- `tests/test_file_tools.py`

#### 验收标准

- 新增 `patch_file` 单元测试，覆盖：
  - 单 edit 成功
  - 多 edit 成功
  - 多重匹配但 `expected_replacements=1` 失败
  - edit 中途失败时不产生半成功脏状态
- 实现明确采用“内存应用 + 单次写回”而不是逐 edit 写盘
- prompt 默认优先引导 agent 使用 `patch_file` 进行局部修改
- 至少一个现有 Custom 任务的 trajectory 能观察到 `patch_file` 替代整文件重写

---

### Change 2：把“失败后的实现优先修复”升级为显式策略，而不是软提示

#### 问题

当前系统已有一定的 correction guidance，但仍然偏软，而且当前状态机还不够精确：

- 当前恢复逻辑把“普通可恢复工具失败”和“verification failure 后的恢复”压在同一个恢复位上；如果直接把更强的 verification-only 约束挂上去，会误伤普通编辑失败
- verification failure 后，agent 仍会跳去改测试
- retry 周期中容易从一个实现文件扩散到多个无关文件
- 对“先实现、后测试”的约束主要存在于 prompt，而不是 loop policy

#### 设计

本 Change 拆成两个子步骤：

#### 2a. 先拆状态，再加策略

新增显式恢复状态，而不是继续复用单一布尔位。推荐模型：

```python
recovery_mode = "none" | "tool_error" | "verification"
```

或语义等价的两个独立状态位：

- `awaiting_retry_after_tool_error`
- `awaiting_retry_after_verification`

要求：

- 现有 `retry_edit_target` 和“先修一个文件再 rerun”纪律，仅绑定到 `tool_error` 恢复窗口
- 新的“实现优先 / 测试编辑守门 / verification recovery 限域”只绑定到 `verification` 恢复窗口
- 成功工具批次或验证通过后，相关恢复状态必须 reset

#### 2b. 仅对 verification recovery 启用更强约束

在 `verification` 恢复窗口中，启用以下策略：

1. **实现优先**
   - 若失败摘要中存在明确 failing target，优先锁定对应实现文件或 benchmark metadata 提供的 `expected_patch_targets`
   - 如果 `expected_patch_targets` 同时包含实现文件与测试文件，优先从实现文件开始，除非 benchmark metadata 明确授权测试补丁

2. **重试窗口限域**
   - 在一次 verification failure 到下一次 verification run 之间，允许编辑的**实现文件**数上限设为 `2`
   - 超出后注入明确反馈，要求先 rerun verification

3. **测试编辑守门**
   - 仅在以下任一条件成立时允许测试编辑：
     - benchmark metadata 明确给出 `authorized_test_edit_paths`
     - benchmark 的官方 `test_patch` / `verification_files` 派生出了受控 regression test patch 集
     - failing target 本身就在测试文件中，且问题属于 fixture / harness 侧
     - 当前任务为 from-scratch 任务，且 agent 自己同时生成了实现与测试
   - 对 SWE / 其他 benchmark，需要先区分：
     - **benchmark 授权的 regression test patch**
     - **agent 自己漂移去改未授权测试**
   - 只有前者允许；后者视为 drift 并阻止

4. **过早完成约束**
   - 对“未调用任何 verification 命令就尝试完成”的情况继续拦截
   - 若连续两次 completion 都在同一 verification failure 上被打回，则强制注入“先读失败块再编辑”的明确反馈

#### 建议新增配置

```python
experiment_config = {
    "max_impl_edit_files_per_verification_recovery": 2,
    "allow_unlisted_test_edits_during_verification_recovery": False,
    "prefer_expected_patch_targets": True,
}
```

#### 文件范围

- `coder_agent/core/agent_turns.py`
- `coder_agent/core/agent_tool_batch.py`
- `coder_agent/core/agent_errors.py`
- `coder_agent/core/agent_loop.py`
- `coder_agent/eval/benchmarks/swebench/loader.py`
- `tests/test_agent_termination.py`
- `tests/test_eval_runner.py`
- `tests/test_swebench_benchmark.py`

#### 验收标准

- 新增测试覆盖：
  - 普通工具失败后的恢复窗口不被 verification-only 策略误伤
  - verification failure 后编辑未授权测试文件被阻止
  - verification failure 后编辑 benchmark 授权测试文件仍可通过
  - verification failure 后在两个实现文件内修复被允许
  - 连续 completion without effective fix 时注入更强反馈
- failing trajectories 中“test drift”占比下降
- 不引入 `C3` Custom suite 的显著回归

---

### Change 3：对 ad hoc 安装依赖建立预算，而不是完全依赖模型克制

#### 问题

SWE-bench promoted failures 已经从 host contamination 逐步转向 task-level failure，但 trajectory 仍会出现 agent 在 repo 内反复尝试安装依赖的情况。这有两个问题：

- 容易把真实实现问题掩盖成环境噪声
- agent 会把大量步骤消耗在低收益安装尝试上

#### 设计

不直接禁止安装，而是引入**任务级安装预算**与更明确的 guidance。

关键实现前提先写死：

- 当前 tool 调用是并行 dispatch 的
- 真正可靠地拿到 shell 命令文本的地方是在 `run_command` tool input / `RunCommandTool.execute()`
- 因此“超预算后不执行命令”不能只停留在 loop 层描述，必须落在 **pre-dispatch** 或 **`RunCommandTool` 内部** 其中之一

`v0.7.0` 的首选方案是双层守卫：

1. **主守卫：pre-dispatch budget guard**
   - 在 `tools/execute.py` 中识别 `run_command` 调用
   - 对 install 类命令做预算判定
   - 超预算时直接生成 deterministic tool error，不进入 shell spawn

2. **防御性守卫：`RunCommandTool.execute()`**
   - 对直接调用 / 单测场景做同样的预算保护
   - 保证即使绕过 pre-dispatch，也不会实际执行超预算安装命令

此外，安装预算必须是 **task-scoped** 的：

- 在每个顶层 agent 任务开始时 reset
- 不影响 benchmark harness 自己执行的 `setup_commands`
- 不影响 task-local env provisioning 的正式路径

#### 批次内确定性协议

当前 dispatcher 是并行批处理，因此预算判定必须在 dispatch 前一次性完成，不能依赖并发执行时的竞争结果。

单个模型回合内若同时出现多个 install 类 `run_command`，规则固定如下：

1. 按模型返回的原始 `tool_calls` 顺序扫描整个批次
2. 识别出其中属于 install 类命令的 `run_command`
3. 根据当前任务剩余预算，只放行前 `N` 个 install 命令
4. 其余 install 命令在 pre-dispatch 阶段直接改写为 deterministic tool error
5. 非 install 的 tool calls 不受影响，继续进入同一批次执行

在 `max_ad_hoc_installs_per_task = 1` 时，若同一批次出现三个 install 类命令，则：

- 第一个 install 按原始顺序允许执行
- 第二、第三个 install 直接返回 “budget exceeded” tool error
- 同批次里的 read / patch / 非 install `run_command` 继续执行

这条规则是唯一接受的实现语义。不得使用“先完成者成功”或其他依赖并发调度结果的策略。

1. 识别安装类命令：
   - `pip install`
   - `python -m pip install`
   - `uv pip install`

2. 维护 `state.ad_hoc_install_count`

3. 新增配置：

```python
experiment_config = {
    "max_ad_hoc_installs_per_task": 1,
}
```

4. 超出预算后，不执行命令，直接返回 tool-level error：
   - 说明已超出任务安装预算
   - 提示先检查本地 import path、task-local venv、existing setup commands

5. ImportError guidance 增强：
   - 明确区分 project-local import 问题和 third-party package 缺失
   - 在已有本地候选模块时，明确提示“不要先安装”

#### 文件范围

- `coder_agent/core/agent_loop.py`
- `coder_agent/tools/execute.py`
- `coder_agent/core/agent_errors.py`
- `coder_agent/tools/shell_tool.py`
- `tests/test_shell_tool.py`
- `tests/test_agent_errors.py`

#### 验收标准

- 新增安装预算单元测试
- 同一批次多个 install 命令时，预算按原始 tool call 顺序确定放行集合
- 在预算为 `1` 时，只允许批次中的第一个 install 执行；后续 install 全部 deterministic fail
- 超预算的 install 命令返回 tool error，且不会实际 spawn shell 进程
- 混合批次中，非 install 的 `run_command` 仍可执行
- SWE 任务轨迹中重复安装行为减少
- 不影响 task-local setup commands 的正式执行路径

---

### Change 4：扩展 SWE-bench 子集，让 benchmark 从“能冒烟”变成“能比较”

#### 问题

当前 SWE 子集结构是：

- smoke: `1` 题
- promoted compare: `3` 题

这足够证明 harness 已接通，但不足以稳定比较 agent 能力，也容易被单题偶然性放大。

#### 设计

将 SWE-bench 固定子集拆成两层：

1. **infra smoke**
   - 从 `1` 题扩到 `3` 题
   - 覆盖不同 repo / 不同测试命令形态
   - 目标是尽快发现 checkout / env / interpreter / test overlay 问题

2. **capability compare**
   - promoted subset 从 `3` 题扩到 `8-12` 题
   - 至少覆盖 `5` 个上游仓库
   - 同时保留 fixed manifest + local overrides + hash audit 契约

新增要求：

- 每个 promoted task 必须标注：
  - repo
  - python version
  - `setup_complexity`
  - expected patch target count
  - `primary_failure_mode_category`
  - `authorized_test_edit_paths`

- analysis 输出按以下维度拆分：
  - infra/setup failure
  - dependency noise
  - tool-protocol / provider issue
  - wrong-file edit
  - test drift
  - genuine implementation miss

#### machine-readable 输出协议

本计划不扩展现有 `*_comparison_report.json` 作为分层分析主出口；comparison report 继续只承担 compare summary 角色。

分层分析统一通过新增的 **analysis report** 落地，约定如下：

- 产物名：`results/<experiment_id>_analysis_report.json`
- 生成入口：`coder_agent cli analyze <experiment_id>`
- 主写出位置：`coder_agent/eval/analysis.py`
- CLI 负责触发与打印路径：`coder_agent/cli/analyze.py`

最小 JSON schema：

```json
{
  "experiment_id": "custom_v070_cmp_C3",
  "generated_at": "2026-04-08T12:00:00Z",
  "summary": {
    "total_failed": 0,
    "layered_failure_counts": {
      "infra_setup_failure": 0,
      "dependency_noise": 0,
      "tool_protocol_or_provider": 0,
      "wrong_file_edit": 0,
      "test_drift": 0,
      "genuine_implementation_miss": 0
    }
  },
  "per_task": [
    {
      "task_id": "sympy__sympy-22005",
      "termination_reason": "retry_exhausted",
      "primary_category": "genuine_implementation_miss",
      "secondary_signals": ["dependency_noise"],
      "notes": "..."
    }
  ]
}
```

若后续需要 compare-aware layered summary，应由 comparison manifest 额外引用这些 per-experiment analysis reports，而不是把 layered schema 混进现有 comparison report。

#### 文件范围

- `coder_agent/eval/benchmarks/swebench/official_tasks.source.json`
- `coder_agent/eval/benchmarks/swebench/official_manifest.generated.json`
- `coder_agent/eval/benchmarks/swebench/local_overrides.json`
- `coder_agent/eval/benchmarks/swebench/manifest_export.py`
- `coder_agent/eval/benchmarks/swebench/loader.py`
- `coder_agent/eval/analysis_taxonomy.py`
- `coder_agent/eval/analysis.py`
- `coder_agent/cli/analyze.py`
- `coder_agent/eval/eval_compare.py`（仅在后续决定让 comparison manifest 引用 analysis report 时）
- `tests/test_swebench_benchmark.py`
- `tests/test_cli_eval.py`
- `README.md`
- `report/REBASELINE_PLAYBOOK_0_7_0.md`（落地时新增）

#### 验收标准

- loader allowlist / manifest export / local overrides 三者对新增字段完全对齐
- loader 测试覆盖新的 smoke / promoted task 集
- compare lanes 使用一致的 manifest/override hash
- `analyze` CLI 能输出 infra-vs-agent 分层结果，而不只是通用 taxonomy
- `results/<experiment_id>_analysis_report.json` 被稳定写出，且符合约定 schema
- 若 comparison manifest 选择引用 analysis report，则引用路径必须可解析到对应的 `analysis_report.json`
- 扩展后的 SWE 子集不再依赖 host env
- 结果分析能明确区分 infra 问题与 agent 质量问题

---

### Change 5：对 `C4` memory lane 做一次干净、有限、可决策的复核

#### 问题

从历史结果看：

- `0.5.1` 时 `C4 similarity` 曾优于 shipped `C4` default
- 到 `0.6.0` targeted compare 时，`C4` 反而落后于 `C3`

这意味着 memory lane 当前处于“有潜力，但没有最新证据支持 promotion”的状态。

#### 设计

在 `v0.7.0` 中，不默认继续堆 memory 机制，而是做一次收敛性的复核：

1. 保持当前 shipped `C3/C4/C6` lanes 不变
2. 新增一个受控 compare lane：

```json
{
  "memory_lookup_mode": "similarity",
  "keep_recent_turns": 4
}
```

3. 仅回答两个问题：
   - `C4 similarity` 在 `0.6.0` runtime contract 上是否稳定优于当前 `C4 default`
   - 它是否能接近或超过 `C3`

4. 决策规则：
   - **默认决策依据只看 Custom compare artifact**
   - 若连续 compare 中至少稳定多赢 `1` 个 Custom task，可作为 `0.7.x` 后续 promotion candidate
   - 否则 `C4` 维持 supporting/experimental，不继续扩 memory 功能面

5. 可选 supporting evidence：
   - 若希望让 SWE 对 `C4 similarity` 提供 veto/supporting evidence，必须显式新增一个 supporting artifact，例如：
     - `swe_smoke_c4_similarity_<tag>`
     - 或 `swe_probe_c4_similarity_<tag>`
   - 该 artifact 只作为 supporting lane，不自动进入正式 SWE compare matrix
   - 若没有这个 supporting artifact，就不得在 promotion 结论里引用“SWE 噪声是否放大”

6. Compare policy 对齐要求：
   - 在 `v0.7.0` 中，正式 SWE compare matrix 仍保持 `C3 vs C6`
   - 只有当新的 playbook 明确提升 compare policy 时，`C4` 才能进入 SWE 正式 compare matrix

#### 文件范围

- `config.yaml`（仅在决定 promotion 时）
- `README.md`（仅在决定 promotion 时）
- `report/BASELINE_0_7_0.md`（落地时）
- `report/IMPROVEMENT_REPORT_v0.7.0*.md`（落地时）

#### 验收标准

- compare artifact 完整，含 manifest 与 trajectories
- 若引用 SWE 作为 supporting evidence，则对应 `C4 similarity` SWE artifact 也必须存在
- 若未生成该 supporting artifact，则 promotion 结论明确标注为 “Custom-only decision”
- 结论以 artifact 为准，不再依赖单次体验判断

---

### Change 6：整理版本与文档语义，消除轻度漂移

#### 问题

当前存在几类可见漂移：

- `pyproject.toml` package version 仍是 `0.4.5`
- README 和 baseline 文档已经在讲 `0.6.0`
- `config.yaml` 注释中仍残留部分旧语义

这不会直接影响 agent 能力，但会持续增加维护摩擦。

#### 设计

建立一个简单而明确的版本约定：

1. `pyproject.toml` 的 `project.version` 表示当前代码线版本
2. `report/BASELINE_x_y_z.md` 表示 accepted benchmark cycle
3. README 的 Current Status 必须同时给出：
   - 当前代码版本
   - 当前 accepted baseline

若短期不希望频繁改 package version，也至少要在 README 里明确两者并列，而不是隐含混用。

#### 文件范围

- `pyproject.toml`
- `README.md`
- `config.yaml`
- `coder_agent/__init__.py`（如需要显式导出版本）

#### 验收标准

- 新读者不需要翻 report 才能理解当前分支状态
- README、package version、baseline cycle 不再互相矛盾

---

## 4. 实施顺序

推荐顺序：

1. **先做 Change 1**
   - 这是最可能直接抬高 repo-repair 上限的基础能力

2. **再做 Change 2a 与 Change 3 的拦截点基础设施**
   - 先把恢复状态拆开，并把安装预算拦截点放到正确层级

3. **然后做 Change 4 的 metadata / analysis contract**
   - 先补 loader allowlist、manifest export、analysis/CLI 出口，再扩 SWE 子集

4. **再完成 Change 2b 与 Change 3 的完整策略**
   - 这时 verification-aware lifecycle、测试编辑守门和安装预算才真正闭环

5. **然后做 Change 4 的子集扩展**
   - 在契约与行为改进后扩大 SWE 子集，才能更真实地测到能力变化

6. **最后做 Change 5 与 Change 6**
   - 一个用于决定 memory lane 去留
   - 一个用于整理外部语义与版本表述

---

## 5. 验证与 Rebaseline 要求

### 5.1 本地 gate

```bash
uv run pytest
uv run python -m coder_agent --help
uv run python -m coder_agent eval --help
```

### 5.2 新增 focused regression

至少新增以下测试块：

- `tests/test_file_tools.py`
  - `patch_file` 全覆盖
- `tests/test_shell_tool.py`
  - ad hoc install budget
- `tests/test_agent_errors.py`
  - import/install guidance
- `tests/test_agent_termination.py`
  - retry/test-edit policy
- `tests/test_swebench_benchmark.py`
  - 扩展的 SWE smoke/promoted loader 与 metadata contract
- `tests/test_cli_eval.py`
  - analysis/report entrypoint 或 compare/report wiring 的 CLI 覆盖

### 5.3 Benchmark gate

`v0.7.0` 至少需要以下 artifact：

- Custom targeted compare: `C3`, `C4`, `C6`
- Custom supporting compare: `C4 similarity`（若执行 Change 5）
- SWE smoke rerun: 扩展后的 smoke subset
- SWE promoted compare rerun: 扩展后的 promoted subset（正式 matrix 仍为 `C3/C6`）
- 可选 supporting lane：`C4 similarity` SWE smoke/probe artifact（仅当 Change 5 需要 SWE supporting evidence 时）

### 5.4 接受标准

`v0.7.0` 不强制要求立刻拿到很高的 SWE 绝对分数，但必须满足以下至少三项：

- Custom `C3` 不出现明显回归
- SWE smoke 不再出现 host-env contamination
- SWE promoted compare 至少出现比 `0.6.0` 更干净的 failure distribution
- failing trajectories 中 test drift / repeated install 占比下降
- 至少一个 repo-repair 任务能明确受益于 `patch_file`

---

## 6. 风险与决策点

### 风险 A：`patch_file` 增加 tool schema 复杂度

缓解方式：

- 保持 schema 极简
- 不一次性做 unified diff parser
- 优先支持“顺序 edit 列表 + expected replacements”模型

### 风险 B：行为约束过强，压低简单任务成功率

缓解方式：

- 先拆出 `verification` 专属恢复窗口，再只在该窗口内启用强约束
- from-scratch 任务保留测试编辑例外

### 风险 C：SWE 子集扩展后，infra 成本重新抬头

缓解方式：

- 先扩 smoke，再扩 promoted
- 每加入一个新 task，都必须补 local override 和 focused test

### 风险 D：memory lane 继续分散注意力

缓解方式：

- `v0.7.0` 只做一次有限复核，不把 memory 作为主线功能扩展

---

## 7. 结论

`v0.7.0` 的正确方向不是继续补“运行时基础设施”，因为 `0.6.0` 已经基本完成了那条线；真正限制项目上限的，是**编辑原语太弱、失败恢复约束不够硬、benchmark 还不够高信号**。

如果 `v0.7.0` 能完成本计划，项目会从“工程化很强的 agent 评测平台”更进一步，进入“开始具备稳定 repo-repair 提升路径”的阶段。届时再考虑 HTTP API、job manager 或多 agent，才有更扎实的基础。
