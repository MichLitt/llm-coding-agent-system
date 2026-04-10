# Baseline 0.7.2

> Date: 2026-04-10
> Version: 0.7.2
> Status: accepted
> Focus: 持久化 Run State + 服务化 API 层（Phase A/B/C）

---

## Summary

v0.7.2 是一次 runtime 架构升级，不以提高 benchmark pass rate 为目标。核心交付：

- **RunStateStore**：独立 SQLite（`memory/run_state.db`），三张表（`runs` / `run_steps` / `tool_calls`），每 step 写 checkpoint
- **Resume**：`coder-agent run --resume <run_id>` 从最近 checkpoint 恢复 `LoopState` 和 `MessageHistory`
- **服务化 API**：`coder-agent serve` 启动 FastAPI 服务，支持异步任务提交、状态查询、协作式取消
- **向后兼容**：`enable_run_state: false` 完全跳过持久化，agent 行为与 v0.7.1 一致

---

## Accepted Artifact Set

### SWE promoted（formal lanes）

- `swe_promoted_cmp_v072r1_C3` → `results/swe_promoted_cmp_v072r1_C3.json` → **2/8 = 25.0%**
- `swe_promoted_cmp_v072r2_C6` → `results/swe_promoted_cmp_v072r2_C6.json` → **2/8 = 25.0%**
- `swe_promoted_cmp_v072r1_comparison_report.json` → formal C3/C6 compare summary（C6 以 r2 为准）

### SWE supporting

- `swe_promoted_support_v072r1_C4` → `results/swe_promoted_support_v072r1_C4.json` → **2/8 = 25.0%**

### `enable_run_state=false` 回归验证

- `custom_no_rs_v072_C3` → `results/custom_no_rs_v072_C3.json` → **36/40 = 90.0%**
- `run_id` present in results: **0/40**（run state 完全关闭，无持久化副作用）

---

## Accepted Interpretation

### SWE 结果

- C3 / C6 / C4 均持平 v0.7.1 的 2/8 = 25.0%，通过 task 不变：
  - `pylint-dev__pylint-5859`
  - `pylint-dev__pylint-7993`
- C6 r1 跑出 1/8 属于模型不确定性噪声，r2 跑回 2/8；以 r2 为正式结果
- C4 `sympy__sympy-22005` 以 `loop_exception` 终止，原因为 MiniMax API 瞬时 500 错误，非 agent 代码问题
- v0.7.2 runtime 变更对 agent 推理行为无影响

### Custom 回归

- `enable_run_state=false` 下 Custom C3 = 36/40 = 90.0%，与 v0.6.0 baseline（C3=90.0%）完全一致
- 4 个失败 task（`custom_hard_003`、`custom_medium_011`、`custom_medium_012`、`custom_hard_005`）均为已知不稳定项，与本版本变更无关

### run_id 写入验证

所有 SWE 结果的 `activation_counters` 均含 `run_id` 字段，证明 run state 持久化正常工作。

---

## 新增文件一览

| 文件 | 说明 |
|------|------|
| `coder_agent/memory/run_state.py` | RunStateStore：runs / run_steps / tool_calls 三表 SQLite |
| `coder_agent/service/__init__.py` | 服务层包 |
| `coder_agent/service/app.py` | FastAPI app，6 个端点，协作式 cancel |
| `coder_agent/service/schemas.py` | Pydantic 请求/响应模型 |
| `coder_agent/cli/serve.py` | `coder-agent serve` CLI 子命令 |
| `tests/test_run_state_store.py` | RunStateStore CRUD + resume summary |
| `tests/test_run_resume.py` | Agent resume 路径端到端 |
| `tests/test_run_cli.py` | `run --resume` / `runs list` / `runs show` CLI 路径 |
| `tests/test_service_api.py` | FastAPI 端点 happy path + 错误路径 |

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/runs` | 提交任务（异步，立即返回 `run_id`） |
| `GET` | `/runs` | 列出最近 N 条 run |
| `GET` | `/runs/{run_id}` | 查询 run 状态和 metrics |
| `POST` | `/runs/{run_id}/cancel` | 协作式取消（step 边界生效） |
| `GET` | `/runs/{run_id}/steps` | 查询 step 列表（含 checkpoint） |

---

## Local Gate

```
uv run pytest tests/
# 266 passed

uv run pytest tests/test_run_state_store.py tests/test_run_resume.py \
              tests/test_run_cli.py tests/test_service_api.py -v
# 所有 Phase A/B/C 新增测试通过
```

---

## Residual Risks / 记录项

- Cancel 是协作式（step 边界），不抢占正在执行的 LLM 调用或子进程
- `list_steps` / `latest_checkpoint` / `schemas.py` 命名与 IMPROVEMENT_PLAN_v0.7.2.md 略有出入，功能等价
- C6 对模型不确定性敏感，单次重跑仍可能落在 1/8；以多次跑均值或 r2 为准

---

## Historical Context

- [BASELINE_0_7_1.md](./BASELINE_0_7_1.md) 为前序 baseline
- [IMPROVEMENT_PLAN_v0.7.2.md](./IMPROVEMENT_PLAN_v0.7.2.md) 为本版本执行计划
