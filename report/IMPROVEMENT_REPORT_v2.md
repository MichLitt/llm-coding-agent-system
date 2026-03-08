# Coder-Agent 改进报告 v2

> 基于评测数据的问题分析、针对性修改与效果验证

---

## 一、旧版问题诊断

### 1.1 旧版实验数据（v1）

#### Custom 全量对比（11 题，旧版 C1~C4）

| Config | Strict Success | Partial Credit | Retry Cost | Avg Steps | Avg Duration |
|---|---:|---:|---:|---:|---:|
| C1 | 63.6% | 86.4% | 4.2% | 10.5 | 68.3s |
| C2 | 72.7% | 95.5% | 4.9% | 11.0 | 98.4s |
| C3 | 63.6% | 86.4% | 4.8% | 10.5 | 69.0s |
| C4 | 63.6% | 95.5% | 1.8% | 10.5 | 65.5s |

> 注：旧版报告 schema 中 `Strict Success` 对应 `task_success_rate`，尚未区分 `Benchmark Pass` 和 `Clean Completion`。

#### HumanEval 旧版子集（5 题）

| Config | Strict Success | Partial Credit |
|---|---:|---:|
| C1 | 20.0% | 100.0% |
| C2 | 0.0% | 100.0% |
| C3 | 20.0% | 80.0% |
| C4 | 60.0% | 100.0% |

---

### 1.2 根因分析

通过代码审查与轨迹分析，识别出三个相互独立的核心问题：

#### 问题 1：Agent 不知道该停（终止逻辑缺陷）

**现象**：Custom benchmark pass 高达 90.9%，但 clean completion 只有 54.5%~72.7%，两者 gap 达 18~36pp。大量任务"代码写对了但 agent 还在跑"。

**根因**：
- 主循环为 `for _ in range(max_steps)`，没有明确的提前退出机制
- System prompt 只说"完成后 summarize"，没有明确禁止任务完成后继续调用工具
- Retry 次数超出 `max_retries` 后不主动停止，继续空跑直到 timeout

```python
# 旧版：循环跑满 max_steps，超时才退
for _ in range(cfg.agent.max_steps):
    ...
# 循环结束 → final_status = "timeout"
```

#### 问题 2：C1~C4 配置行为不独立（开关未生效）

**现象**：C1/C2/C3 的 Strict Success 几乎相同（63.6%~72.7%），说明三种配置没有产生实质性行为差异。C2（0% Strict Success on HumanEval）甚至不如 C1（20%），说明"ReAct 模式"开关未真正影响行为。

**根因**：通过代码审查发现：

| 配置维度 | 是否真正生效 | 原因 |
|---|---|---|
| `planning_mode` (react/direct) | **否** | `_build_system_prompt()` 不读此字段，C1/C2 system prompt 相同 |
| `correction` (True/False) | **否** | Self-correction hint 注入逻辑硬编码，`correction` 字段未被读取 |
| `memory` (True/False) | **是** | `_make_agent()` 中有条件判断，C4 会注入最近任务摘要 |

结论：**C1 = C2 = C3**（运行时行为完全相同）；只有 C4 因 memory 注入有实质差异。

#### 问题 3：HumanEval 全量数据缺失

**现象**：HumanEval 证据仅有 3~5 题的 smoke-level 数据，且旧版全量运行在第 146 题时被中断，结果文件未写入。

**根因**：`run_suite()` 在所有任务完成后才一次性写结果文件，任何中断都导致数据全部丢失。

---

## 二、针对性改动

### 2.1 修复 Agent 终止逻辑

**文件**：`coder_agent/core/agent.py`

**改动 1：强化 system prompt 的停止指令**

在 `_build_system_prompt()` Guidelines 段落中新增明确的完成退出指令：

```
- When ALL required tasks are done and verified (tests pass, files created, etc.),
  stop calling tools and respond with a final summary only. Do NOT keep calling
  tools after the task is complete.
```

**改动 2：retry 耗尽时主动退出**

```python
# 新增：retry 超限时主动 return，不再空跑到 timeout
if retry_count > cfg.agent.max_retries:
    ...
    return TurnResult(..., final_status="failed")
```

### 2.2 让 C1~C4 真正行为独立

**文件**：`coder_agent/config.py`、`coder_agent/core/agent.py`

**改动 1：`AgentConfig` 新增 `enable_correction` 字段**

```python
enable_correction: bool = os.environ.get(
    "CODER_CORRECTION_ENABLED",
    str(_Y.get("agent", {}).get("enable_correction", True))
).lower() == "true"
```

**改动 2：`_build_system_prompt()` 参数化**

将模块级函数改为接受 `planning_mode` 和 `enable_correction` 参数，根据配置生成不同内容：

```python
def _build_system_prompt(
    planning_mode: str = "react",
    enable_correction: bool = True,
    ...
) -> str:
    # C1 direct 模式
    if planning_mode == "direct":
        planning_instruction = (
            "Generate the complete solution directly. "
            "You may use tools to read existing files or run code, "
            "but avoid lengthy step-by-step exploration — go straight to writing and verifying."
        )
    else:
        # C2/C3/C4 react 模式
        planning_instruction = "Think step by step before each action. ..."

    # C3/C4 才包含 Self-correction rules 段落
    if enable_correction:
        correction_section = "Self-correction rules: ..."
    else:
        correction_section = ""
```

**改动 3：`Agent.__init__` 根据 `experiment_config` 动态构建 system prompt**

```python
if system is not None:
    self.system = system
else:
    planning_mode = self.experiment_config.get("planning_mode", cfg.agent.planning_mode)
    enable_correction = self.experiment_config.get("correction", cfg.agent.enable_correction)
    self.system = _build_system_prompt(
        planning_mode=planning_mode,
        enable_correction=enable_correction,
    )
```

**改动 4：`_loop()` 中 correction hint 注入改为条件执行**

```python
# 旧版：始终注入
if detected_error and detected_error != last_error_type:
    combined_observation += f"\n\n[Self-correction hint]: {guidance}"

# 新版：读取配置开关
correction_enabled = self.experiment_config.get("correction", cfg.agent.enable_correction)
if correction_enabled and detected_error and detected_error != last_error_type:
    combined_observation += f"\n\n[Self-correction hint]: {guidance}"
```

### 2.3 改动对照表

| 改动 | 目标问题 | 文件 |
|---|---|---|
| system prompt 新增"完成后停止"指令 | 终止逻辑 | `core/agent.py` |
| retry 耗尽时主动 return | 终止逻辑 | `core/agent.py` |
| `AgentConfig.enable_correction` 字段 | C1~C4 独立性 | `config.py` |
| `_build_system_prompt()` 参数化 | C1~C4 独立性 | `core/agent.py` |
| `Agent.__init__` 动态构建 system prompt | C1~C4 独立性 | `core/agent.py` |
| `_loop()` correction 条件注入 | C1~C4 独立性 | `core/agent.py` |

---

## 三、新版实验结果

### 3.1 Custom 全量对比（11 题，v2）

| Config | Benchmark Pass | Clean Completion | Strict Success | Partial Credit | Retry Cost | Avg Steps | Avg Duration |
|---|---:|---:|---:|---:|---:|---:|---:|
| C1（direct，无 correction） | **100.0%** | 81.8% | 81.8% | **100.0%** | **0.0%** | **6.5** | 51.1s |
| C2（react，无 correction） | **100.0%** | 81.8% | 81.8% | **100.0%** | **0.0%** | 7.1 | 50.3s |
| C3（react + correction） | 90.9% | 81.8% | 81.8% | 90.9% | 0.9% | 7.2 | 56.6s |
| **C4（react + correction + memory）** | **100.0%** | **100.0%** | **100.0%** | **100.0%** | 0.6% | 7.5 | 53.4s |

### 3.2 HumanEval 20 题批量（v2，首次获得超 5 题数据）

| 指标 | 值 |
|---|---:|
| Tasks | 20 |
| Benchmark Pass | **95.0%** (19/20) |
| Clean Completion | 65.0% (13/20) |
| Strict Success | 65.0% |
| Partial Credit | 95.0% |
| Avg Steps | 4.0 |
| Avg Tokens | 410 |

**Per-task breakdown**：

| 结果类型 | 任务 |
|---|---|
| Benchmark pass + clean exit | HumanEval_0,1,3,4,5,7,8,9,12,13,15,16,17（13题） |
| Benchmark pass，未 clean exit | HumanEval_2,6,10,11,14,19（6题） |
| Benchmark miss（solution.py 未创建） | HumanEval_18（1题） |

---

## 四、改进效果分析

### 4.1 Custom 基准：v1 vs v2

| 指标 | v1 最佳（C2） | v2 最佳（C4） | 提升 |
|---|---:|---:|---:|
| Benchmark Pass | 90.9%* | **100.0%** | +9pp |
| Strict Success | 72.7% | **100.0%** | **+27pp** |
| Avg Steps | 11.0 | 7.5 | -3.5步（-32%）|
| Avg Duration | 98.4s | 53.4s | -45s（-46%）|
| Retry Cost | 4.9% | 0.6% | -4.3pp |

> *旧版 `Benchmark Pass` 与 `Strict Success` 使用同一字段，新版已拆分。

**关键结论**：
- **终止逻辑修复效果显著**：Strict Success 提升 27pp，步骤数和时长均大幅下降，说明 agent 在任务完成后确实不再继续空跑。
- **C4 达到 100% Strict Success**：memory 注入（最近 3 个任务摘要）对 clean completion 有正向增益，C4 是迄今最强配置。
- **效率显著提升**：平均步骤数从 11.0 降到 7.5，平均时长从 98.4s 降到 53.4s，说明 agent 路径更直接。

### 4.2 C1~C4 行为差异验证

| 对比 | 差异来源 | 验证指标 |
|---|---|---|
| C1 vs C2 | system prompt planning 指令不同 | C1 avg steps = 6.5，C2 = 7.1（C1 更直接） |
| C2 vs C3 | correction hint 注入开关 | C3 retry cost = 0.9%（有 correction 触发），C2 = 0.0% |
| C3 vs C4 | memory 注入 | C4 clean completion = 100%，C3 = 81.8% |

三个维度现在均产生可观测的行为差异，对比实验具备基本的因果性。

### 4.3 C3 Benchmark Pass 低于 C1/C2 的分析

C3（90.9%，1 题 benchmark 失败）低于 C1/C2（100%），这是一个反直觉结果。

可能原因：correction 开启后，当某个工具执行出现轻微错误时，agent 会进入 correction 路径，但 correction 的修复策略（重写函数、安装依赖等）可能对本来不需要修复的任务造成干扰，引入了新的错误。这说明 **correction 策略需要更精确的触发条件**，而不是只要检测到 stderr 就触发。

### 4.4 HumanEval Clean Completion Gap 分析

HumanEval 的 clean completion（65%）明显低于 custom（81.8%~100%）。

**根因**：HumanEval 任务描述是"实现单个函数，写入 solution.py"，任务天然简短（3~4 步即可完成）。但在代码写完、测试通过后，LLM 倾向于继续输出"verification""额外说明"等内容并再次调用工具，导致 `final_status` 未能在正确时机设为 `success`。

具体失败模式：
- **`agent_final_status = "failed"`，`benchmark_passed = true`**：6 题属于此类（HumanEval_2, 6, 10, 11, 14, 19）——代码正确，但 agent 在某步工具调用出错后进入 failed 路径，尽管 benchmark 检查已通过
- **`solution.py not created`**：HumanEval_18，agent 1 步即退出，可能是 LLM 直接给出文本答案而没有调用 `write_file`

---

## 五、下一步改进方向

### P0：HumanEval Clean Completion 提升

当前 HumanEval clean completion = 65%，距离 custom 的水平还有差距。改进方向：

1. **区分 `agent_final_status = "failed"` 的子类型**：当前"工具调用出错"和"任务逻辑失败"都记为 `failed`，但 6 道 HumanEval 题的 benchmark 实际通过了——说明工具出错发生在 benchmark 检查之后的后续步骤，应该在 verification 通过后就不再 propagate 错误状态
2. **针对 HumanEval 类任务的终止提示**：在 system prompt 中针对"实现单个函数"场景加更强的提示："写完函数文件后不要再调用任何工具"

### P1：C3 Correction 策略优化

C3 的 benchmark pass（90.9%）低于 C1/C2（100%），说明 correction 策略有副作用。改进方向：

1. 提高 correction 触发阈值——只在 exit code ≠ 0 且 stderr 非空时触发，避免 warning 级别的输出也触发修复
2. 限制 correction 的修复范围——禁止 correction 路径修改已经通过测试的文件

### P2：HumanEval 全量运行

当前只有 20 题数据。获得完整 HumanEval（164 题）数据是项目展示价值的关键缺口。建议在下一个稳定版本后运行全量实验。

### P3：README + Demo

工程功能完整，当前最直接影响"项目可展示性"的是文档缺口：
- README 中补充 Architecture Diagram（Mermaid）
- 实验数据表格（4 维指标，C1~C4 对比）
- asciinema 终端录屏 demo

---

## 六、数据来源

| 数据集 | 文件路径 |
|---|---|
| v1 Custom 对比 | `results/custom_cmp_comparison_report.json` |
| v1 HumanEval 子集 | `results/humaneval_cmp_comparison_report.json` |
| v2 Custom 对比 | `results/custom_cmp_v2_comparison_report.json` |
| v2 HumanEval 20题 | `results/humaneval_20_v2.json` |
| 旧版 Custom 全量 | `results/custom_full_batch.json` |
