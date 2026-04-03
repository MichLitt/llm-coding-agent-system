# Ablation Report v0.4.5

> Date: 2026-04-02
> Benchmark: custom (40 tasks × 6 presets = 240 runs)
> Version: v0.4.5

---

## 1. Executive Summary

v0.4.5 是本项目的基础设施清理版本。本次的核心工作不是新增特性，而是修复了 **6 个阻碍准确测量的基础设施 Bug**，消除了 v0.4.4 中的假阳性与假阴性，使消融实验结果首次反映代理的真实能力。

**最终结果：C6（verification_gate）以 85% 的通过率取得最高分，较 v0.4.4 同配置提升 +20pp。**

关键发现：
- 单特性贡献排名：`verification_gate (+42.5pp) > checklist (+40.0pp) > memory (+27.5pp)`（均以 C3 为基准）
- ReAct planning 单独使用有害（C2 vs C1 = -17.5pp）；需配合 correction 和上下文机制才能发挥价值
- Bug G（regex double expansion）是 v0.4.4 中 C3/C5 虚高的主因

---

## 2. Bug 修复摘要

| Bug | 文件 | 影响描述 | v0.4.4 假象 |
|-----|------|----------|-------------|
| **A — guidance_crash** | `agent_errors.py` | 相对 import（`.service`）→ `workspace.glob('/service.py')` → `NotImplementedError`，retry guidance 静默崩溃 | C3/C4 部分重试无效 |
| **B — python cmd** | `shell_tool.py` | macOS 无裸 `python` 命令，`python script.py` 返回 `command not found` | 所有涉及运行脚本的任务失败 |
| **C — search absolute glob** | `search_tool.py` | absolute path 传入 `Path.rglob()` 抛 `NotImplementedError`，搜索工具崩溃 | 搜索相关任务失败 |
| **D — edit_file undefined** | `agent_prompt.py`, `agent_turns.py` | prompt 提示使用 `edit_file` 工具，但该工具不存在（实为 `write_file` + `operation="edit"`），触发 `unknown_tool` → 任务立即终止 | C1/C4 等各 preset 均有 task 提前终止 |
| **F — C5 config mismatch** | `factory.py` | `factory.py` 中 C5 的 `memory=True`，与 `ablation.py` 的 `memory=False` 不一致，导致 C5 实际跑的配置偏离设计 | v0.4.4 C5 结果不可信 |
| **G — regex double expansion** | `shell_tool.py` | `python -m pytest` 被 python regex 展开为 `"/venv/python" -m pytest`，随后 pytest regex 再次命中 `pytest`，展开为 `"/venv/python" -m "/venv/python" -m pytest`，导致 `ModuleNotFoundError` | C3/C5/C6 假阳性（pytest 命令失败但任务标记通过） |

**Fix 方案**：Bug A/B/C/D/F 各有针对性修复；Bug G 采用单遍 alternation regex，使 `python -m pytest` / `python` / `pytest` 三种情况互斥展开，彻底消除交互。

---

## 3. Ablation Matrix

| Preset | correction | memory | checklist | verification_gate | planning_mode | 新增特性 |
|--------|-----------|--------|-----------|-------------------|---------------|---------|
| C1 | False | False | False | False | direct | baseline |
| C2 | False | False | False | False | react | +planning=react |
| C3 | True | False | False | False | react | +correction |
| C4 | True | True | False | False | react | +memory |
| C5 | True | False | True | False | react | +checklist |
| C6 | True | False | False | True | react | +verification_gate |

---

## 4. Metric Results

| Config | N | BenchPass | AvgSteps | AvgRetry | Efficiency | RetryCost | AvgTokens |
|--------|---|-----------|----------|----------|------------|-----------|-----------|
| C1 | 40 | **55.0%** | 3.15 | 0.60 | 0.1746 | 0.190 | 621 |
| C2 | 40 | **37.5%** | 3.85 | 0.93 | 0.0974 | 0.240 | 600 |
| C3 | 40 | **42.5%** | 7.83 | 2.15 | 0.0543 | 0.275 | 790 |
| C4 | 40 | **70.0%** | 5.67 | 1.25 | 0.1233 | 0.220 | 790 |
| C5 | 40 | **82.5%** | 5.05 | 0.97 | 0.1634 | 0.193 | 790 |
| C6 | 40 | **85.0%** | 5.12 | 0.72 | 0.1659 | 0.141 | 790 |

> Efficiency = BenchPass / AvgSteps（每步的期望价值）
> RetryCost = AvgRetry / AvgSteps（步骤中重试的占比）

---

## 5. 特性贡献分析

### 5.1 Marginal Deltas（每个特性相对直接前驱的增量）

| Config | vs | 新增特性 | BenchΔ | AvgStepsΔ | RetryCostΔ |
|--------|----|---------|----|---------|------------|
| C2 | C1 | +planning=react | **-17.5pp** | +0.7 | +0.050 |
| C3 | C2 | +correction | **+5.0pp** | +3.98 | +0.035 |
| C4 | C3 | +memory | **+27.5pp** | -2.16 | -0.055 |
| C5 | C3 | +checklist | **+40.0pp** | -2.78 | -0.082 |
| C6 | C3 | +verification_gate | **+42.5pp** | -2.71 | -0.134 |

### 5.2 Cumulative Deltas（相对 C1 全关基准的累计增量）

| Config | 累计特性 | BenchΔ vs C1 | AvgStepsΔ |
|--------|---------|--------------|-----------|
| C2 | react | -17.5pp | +0.70 |
| C3 | react+correction | -12.5pp | +4.68 |
| C4 | react+correction+memory | **+15.0pp** | +2.52 |
| C5 | react+correction+checklist | **+27.5pp** | +1.90 |
| C6 | react+correction+verification_gate | **+30.0pp** | +1.97 |

### 5.3 特性机制解析

**为何 C2（ReAct alone）< C1（direct）？**
ReAct 引入 plan 步骤会生成更多中间推理，但没有 correction 机制时，一旦首次工具调用失败（`tool_nonzero_exit`）即终止——比 direct 模式多了一步失败路径。C2 的 25/40 任务终止于 `tool_nonzero_exit`（vs C1 的 17/40），步骤效率（Efficiency=0.097）也是最低。

**为何 C4（+memory）> C3（+correction alone）大幅跃升 +27.5pp？**
C3 中 correction 允许重试，但无历史上下文时 agent 在同一错误上反复撞墙，导致 `retry_exhausted`（15/40）。Memory 为 agent 提供已尝试路径的记忆，打破死循环：C4 的 `retry_exhausted` 仅 8/40，平均重试步骤从 2.15 降至 1.25。

**为何 C5（+checklist）和 C6（+verification_gate）效果相近但均显著优于 C4？**
两者从 C3 分支，各自解决了不同问题：
- **checklist**：强制结构化任务分解，减少遗漏步骤（C5 avg_retry 降至 0.97，仅 5 个 retry_exhausted）
- **verification_gate**：强制在声明完成前执行验证，消除"幻觉完成"（C6 avg_retry 最低 0.72，RetryCost 最低 0.141）

C6 略优于 C5（85% vs 82.5%），差异在于 C6 的强制验证机制更直接地消除了误报通过。

---

## 6. v0.4.4 → v0.4.5 对比

| Config | v0.4.4 | v0.4.5 | Δ | 主因 |
|--------|--------|--------|---|------|
| C1 | 62.5% | 55.0% | -7.5pp | v0.4.4 Bug G 假阳性消除 |
| C2 | 37.5% | 37.5% | 0pp | 稳定（C2 无重试，Bug G 不影响） |
| C3 | 62.5% | 42.5% | **-20pp** | Bug G 修复消除假阳性（v0.4.4 的 62.5% 虚高）|
| C4 | 62.5% | 70.0% | **+7.5pp** | Bug A/D 修复，retry guidance 真正生效 |
| C5 | 52.5% | 82.5% | **+30pp** | Bug F（配置修正）+ Bug G（假阳性消除后真实值） |
| C6 | 65.0% | 85.0% | **+20pp** | Bug A/D/G 综合修复，verification_gate 真实价值显现 |

**核心结论**：v0.4.4 的 C3=62.5% 是 Bug G 的测量假象。v0.4.5 的真实值 C3=42.5% 更符合预期（无上下文的 correction 效果有限）。C5/C6 的大幅提升反映的是真实的特性价值被准确测量。

---

## 7. 剩余失败分析（C6 视角，15% = 6/40）

C6 失败的 6 个任务：

| 任务 | 终止原因 | 推断失败类型 |
|------|---------|-------------|
| custom_medium_005 | loop_exception | 工具调用异常循环 |
| custom_v8_005 | loop_exception | 工具调用异常循环 |
| custom_medium_011 | retry_exhausted | 逻辑错误（重试无效） |
| custom_hard_004 | max_steps | 任务复杂度超出步骤上限 |
| custom_hard_005 | retry_exhausted | 算法实现错误 |
| custom_hard_010 | max_steps | 任务复杂度超出步骤上限 |

**规律**：
- `loop_exception`（×2）：非基础设施 Bug，属于工具调用异常或 LLM 输出格式问题
- `retry_exhausted`（×2）：agent 的算法逻辑错误，即使有更多重试机会也无法自行修正
- `max_steps`（×2）：任务（特别是 `custom_hard_*`）复杂度超出当前 max_steps 设置

这些失败均非基础设施问题，代表 agent 能力的真实边界。

---

## 8. 下一步建议

1. **C7：checklist + verification_gate 组合**（预期 88-92%）
   - C5 和 C6 分别从 C3 分支，两者各自效果相近。理论上同时开启可进一步提升。

2. **max_steps 自适应**
   - `custom_hard_*` 任务的 max_steps 失败表明固定步骤上限对复杂任务是瓶颈。

3. **公开 benchmark 验证**
   - 引入 HumanEval/HumanEval+ 验证当前能力在标准 benchmark 上的位置。
   - 已有 MBPP（98%）基准，但 MBPP 过于简单，无法区分 preset。

4. **多轮对话任务**
   - 设计需要多轮用户交互的任务集，测试 agent 在对话上下文维护方面的能力。

---

## 附：Termination Reason 分布

| Config | verification_passed | tool_nonzero_exit | retry_exhausted | max_steps | loop_exception | verification_failed |
|--------|--------------------|--------------------|-----------------|-----------|----------------|---------------------|
| C1 | 22 | 17 | 0 | 0 | 1 | 0 |
| C2 | 15 | 25 | 0 | 0 | 0 | 0 |
| C3 | 18 | 0 | 15 | 4 | 2 | 1 |
| C4 | 28 | 0 | 8 | 2 | 1 | 1 |
| C5 | 33 | 0 | 5 | 1 | 0 | 1 |
| C6 | 34 | 0 | 2 | 2 | 2 | 0 |
