# Coder-Agent 改进报告 v4

> 聚焦评测闭环：断点恢复、结果持久化、状态语义收口，以及 C4 全量 HumanEval 结果

---

## 一、本轮目标与落地范围

本轮只收口评测闭环，不扩展 memory/index/context 深层能力。核心目标：

- `eval` 支持 checkpoint + resume
- 单跑路径支持 `--preset C1/C2/C3/C4`
- Agent 失败结果补齐 `termination_reason`
- HumanEval prompt 收紧为“写文件 -> 跑一次 `python solution.py` -> 成功后立即停止”
- 跑通 C4 全量 HumanEval（164 题）

---

## 二、代码改动

### 2.1 Eval 持久化与恢复

**文件**：`coder_agent/eval/runner.py`

- `run_suite()` 改为每题完成后：
  - 追加写 `results/<label>.jsonl`
  - 重写兼容格式 `results/<label>.json`
  - 更新 `results/<label>_run_manifest.json`
- 新增 checkpoint 读取逻辑，`resume=True` 时按 `task_id` 跳过已完成任务
- `manifest` 记录：
  - `benchmark`
  - `preset`
  - `git_commit`
  - `started_at`
  - `finished_at`
  - `completed_task_ids`
  - `total_tasks`
  - `resume_enabled`

### 2.2 单跑 preset 与 CLI

**文件**：`coder_agent/cli/main.py`

- 新增 `--preset default/C1/C2/C3/C4`
- 新增 `--resume`
- `--compare` 与 `--preset` 互斥
- compare 和 single-run 共用同一套 preset 映射，避免配置漂移

### 2.3 Agent 终止原因与 shell 失败语义

**文件**：`coder_agent/core/agent.py`、`coder_agent/tools/shell_tool.py`、`coder_agent/eval/metrics.py`

- `TurnResult` / `EvalResult` 新增 `termination_reason`
- 终止原因固定为：
  - `model_stop`
  - `tool_nonzero_exit`
  - `tool_exception`
  - `retry_exhausted`
  - `loop_exception`
  - `max_steps`
- `stderr` 非空但 `exit code = 0` 不再触发 correction
- shell 工具的 blocked / timed out 改为抛出异常，由 agent 统一归类为 `tool_exception`
- 新增安全打印，避免 Windows 控制台编码导致 `loop_exception`

### 2.4 HumanEval prompt 收紧

**文件**：`coder_agent/eval/benchmarks/humaneval.py`

新 prompt 明确要求：

1. 写 `solution.py`
2. 只运行一次 `python solution.py`
3. 成功后立即停止
4. 禁止额外 verification 命令

### 2.5 Analysis 空指针修复

**文件**：`coder_agent/eval/analysis.py`

- `failure_taxonomy()` 处理 terminal step 中 `action=None` 的情况
- 使 `analyze humaneval_full_c4_v4` 能在真实 trajectory 上正常运行

---

## 三、真实实验结果

### 3.1 Custom smoke（v4）

命令：

```bash
uv run python -m coder_agent eval --benchmark custom --limit 1 --config-label custom_smoke_v4
```

结果：

| Metric | Value |
|---|---:|
| Tasks | 1 |
| Benchmark Pass | 100.0% |
| Clean Completion | 100.0% |
| Strict Success | 100.0% |
| Avg Steps | 4.0 |

### 3.2 HumanEval smoke（3 题，C4，v4）

命令：

```bash
uv run python -m coder_agent eval --benchmark humaneval --limit 3 --preset C4 --config-label humaneval_smoke_c4_v4
```

结果：

| Metric | Value |
|---|---:|
| Tasks | 3 |
| Benchmark Pass | 100.0% |
| Clean Completion | 100.0% |
| Strict Success | 100.0% |
| Avg Steps | 3.0 |

### 3.3 HumanEval 回归（20 题，C4，v4）

命令：

```bash
uv run python -m coder_agent eval --benchmark humaneval --limit 20 --preset C4 --config-label humaneval_20_c4_v4
```

结果：

| Metric | Value |
|---|---:|
| Tasks | 20 |
| Benchmark Pass | 100.0% |
| Clean Completion | 100.0% |
| Strict Success | 100.0% |
| Avg Steps | 3.0 |

**状态语义回归结论**：

- `benchmark_passed=true && agent_final_status != "success"` 的任务数：**0**
- 说明 v3 中 HumanEval 子集里残留的 `failed` 噪声已清零

### 3.4 HumanEval 全量（164 题，C4，v4）

命令：

```bash
uv run python -m coder_agent eval --benchmark humaneval --preset C4 --resume --config-label humaneval_full_c4_v4
```

结果：

| Metric | Value |
|---|---:|
| Tasks | 164 |
| Benchmark Pass | **95.7%** (157/164) |
| Clean Completion | **98.8%** (162/164) |
| Strict Success | **95.7%** (157/164) |
| Partial Credit | **95.7%** |
| Avg Steps | 3.1 |
| Avg Tokens | 410 |
| Avg Duration | 24.7s |
| Retry Cost | 0.1% |

### 3.5 Resume 校验

在全量运行完成后，再次执行相同命令：

```bash
uv run python -m coder_agent eval --benchmark humaneval --preset C4 --resume --config-label humaneval_full_c4_v4
```

结果：

- 164/164 任务全部显示 `SKIP from checkpoint`
- 说明真实 `--resume` 路径已生效，不会重复运行已完成任务

---

## 四、失败分析

### 4.1 全量 HumanEval 失败任务

共 7 题未达到 strict success：

| Task | Benchmark | Final Status | Termination | Steps |
|---|---|---|---|---:|
| HumanEval_28 | ✗ | failed | `loop_exception` | 1 |
| HumanEval_32 | ✗ | success | `model_stop` | 3 |
| HumanEval_83 | ✗ | success | `model_stop` | 3 |
| HumanEval_93 | ✗ | success | `model_stop` | 3 |
| HumanEval_108 | ✗ | success | `model_stop` | 3 |
| HumanEval_145 | ✗ | timeout | `max_steps` | 15 |
| HumanEval_147 | ✗ | success | `model_stop` | 3 |

### 4.2 `analyze humaneval_full_c4_v4` 输出

Trajectory 分析摘要：

- Success: 157 / 164 (95.7%)
- Failed: 6
- Timeout: 1
- Avg steps (failed): 4.3
- Retry rate: 0.4%
- Correction success: 100.0%

当前 taxonomy 输出：

| Category | Count | Fraction |
|---|---:|---:|
| Other | 6 | 85.7% |
| Syntax Error | 1 | 14.3% |

这说明 **当前 failure taxonomy 对“benchmark miss but clean stop”的细分还不够强**，是下一轮可以继续改进的点。

---

## 五、结论

- 评测闭环的工程问题已经基本收口：
  - checkpoint/resume 可用
  - preset 单跑可用
  - `termination_reason` 可解释
  - HumanEval 状态噪声在 20 题回归中清零
- C4 全量 HumanEval 已拿到首个完整基线：**95.7% strict success**
- 下一轮如果继续做评测方向，优先级应为：
  1. 深化 failure taxonomy（尤其是 benchmark miss 的细分类）
  2. 分析 HumanEval_28 / 145 / 147 等失败样本
  3. 视需要再决定是否跑 full compare matrix（C1~C4 全量）

---

## 六、数据来源

| Artifact | Path |
|---|---|
| Custom smoke manifest | `results/custom_smoke_v4_run_manifest.json` |
| HumanEval smoke result | `results/humaneval_smoke_c4_v4.json` |
| HumanEval 20 result | `results/humaneval_20_c4_v4.json` |
| HumanEval 20 manifest | `results/humaneval_20_c4_v4_run_manifest.json` |
| HumanEval full result | `results/humaneval_full_c4_v4.json` |
| HumanEval full checkpoint | `results/humaneval_full_c4_v4.jsonl` |
| HumanEval full manifest | `results/humaneval_full_c4_v4_run_manifest.json` |
| HumanEval full trajectories | `trajectories/humaneval_full_c4_v4.jsonl` |
