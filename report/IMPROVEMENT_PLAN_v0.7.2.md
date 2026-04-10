# Improvement Plan v0.7.2 — 持久化 Run State 与服务化 API 层

**Date:** 2026-04-09  
**Branch:** TBD（从 `main` 切出）  
**Baseline:** v0.7.1 accepted — SWE promoted C3/C6 = 2/8 = 25.0%；Custom 40-task 95%+；HumanEval 95-98%

> **版本定性：** v0.7.2 是一次 runtime 架构升级，不以提高 benchmark pass rate 为目标。目标是把现有"可评测的 agent prototype"升级为"可恢复的 agent runtime"——支持 run 持久化、断点续跑、工具调用审计、以及通过 HTTP API 提交和查询任务。这是路线图 Phase 1 的核心交付。

> **Document status:** 本文档是执行草案。未经合并入主仓前，不作为 playbook 引用依据。

---

## 0. 背景与动机

v0.7.1 完成了评测链路的清理（验证覆盖层修复、pipefail 语义、shell_exit_masking 分类），建立了稳定的 25% SWE promoted baseline。当前系统的主要工程债务已从"benchmark 可信性"转移到"runtime 架构完整性"。

具体问题：

1. **Run 状态不可恢复**：`LoopState` 完全驻留内存，任何进程崩溃、超时或外部中断都会导致整个 run 丢失。当前唯一的 checkpoint 机制是 eval runner 的结果文件（记录 task 完成与否），而非 agent 内部 step 状态。

2. **工具调用无审计 trail**：每次 tool call 的结果仅存在于 `MessageHistory`（内存），history compaction 后即消失。没有独立的 tool call 结构化日志，无法事后重放或对单次 tool call 做细粒度分析。

3. **没有服务入口**：系统只有 CLI，无法被外部系统（如后续 EvalOps Platform）通过 API 调用。任务的提交、状态查询、结果拉取均不可编程化。

4. **可观测性碎片化**：token count、step duration、tool success rate 等指标散落在 `TurnResult`、`LoopState`、trajectory JSONL 中，没有统一的 run-level metrics 结构。

v0.7.2 通过三件事解决上述问题：

- **RunState 持久化**：每个 step 后写 checkpoint，进程恢复后可从断点续跑
- **Tool call audit trail**：独立记录每次 tool call 的结构化日志，与 trajectory 解耦
- **FastAPI 服务层**：提供异步任务提交和 run 查询 API，CLI 保持不变

---

## 1. 版本目标

### 1.1 主要目标

- 每个 agent run 产生一条持久化 `RunRecord`，含完整状态机（pending → running → success/failed/cancelled）
- 每次 tool call 产生一条持久化 `ToolCallRecord`，含工具名、输入、输出、耗时、是否出错
- run 意外中断后可通过 `--resume <run_id>` 从最近 checkpoint 恢复，跳过已完成 step
- `coder-agent serve` 启动 HTTP 服务，支持 `POST /runs`（提交）、`GET /runs/{run_id}`（查状态）、`POST /runs/{run_id}/cancel`（取消）

### 1.2 次要目标

- run-level 结构化 metrics（step count、tool success rate、total tokens、wall duration）随 run 完成一并持久化
- `GET /runs/{run_id}/steps` 返回 step 列表（思考文本 + tool call 摘要 + 观测摘要）
- CLI `run` 命令输出新增 `run_id` 提示，方便手动 resume

### 1.3 非目标

本版本明确不做：

- 多 agent 并发协作
- 前端或 Web UI
- 完整 EvalOps Platform（下一个独立项目）
- SWE-bench pass rate 提升（不改 agent 推理逻辑）
- 认证鉴权（API 无 auth，仅本地使用）
- `C5` checklist 模式相关变更

---

## 2. 架构变更总览

### 2.1 现有架构（v0.7.1）

```
CLI (Click)
  └── Agent.run()
        └── run_agent_loop()          # LoopState 完全内存
              ├── MessageHistory      # 内存 + history compaction
              ├── TrajectoryStore     # JSONL，finish 时写入
              └── MemoryManager       # SQLite：task_history（finish 后写）
```

### 2.2 目标架构（v0.7.2）

```
CLI (Click)                     HTTP Service (FastAPI)
  └── Agent.run()  ◄─────────────────┘
        └── run_agent_loop()
              ├── MessageHistory      # 不变
              ├── RunStateStore       # NEW: SQLite，每 step 写 checkpoint
              │     ├── runs          # run 状态机
              │     ├── run_steps     # 每 step 快照
              │     └── tool_calls    # 每次 tool call 审计
              ├── TrajectoryStore     # 不变（JSONL，finish 时写）
              └── MemoryManager       # 不变（task_history，finish 后写）
```

`RunStateStore` 是新增的独立 SQLite 存储，不修改现有 `MemoryManager` 的 schema。这样可以保持向后兼容，且 run state 数据库可以单独清理或导出。

---

## 3. 数据模型

### 3.1 RunRecord（runs 表）

每个 `agent.run()` 调用对应一条 `RunRecord`。

```sql
CREATE TABLE runs (
    run_id          TEXT PRIMARY KEY,      -- UUID v4
    task_id         TEXT,                  -- eval task id 或 NULL（交互模式）
    experiment_id   TEXT NOT NULL,
    preset          TEXT,                  -- C3 / C4 / C6 / default
    llm_profile     TEXT,
    workspace_path  TEXT,
    task_description TEXT NOT NULL,
    status          TEXT NOT NULL          -- pending / running / success / failed / cancelled / timeout
                    DEFAULT 'pending',
    started_at      REAL,                  -- Unix timestamp（loop 开始时写入）
    finished_at     REAL,                  -- Unix timestamp（loop 结束时写入）
    total_steps     INTEGER DEFAULT 0,
    total_tool_calls INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    tool_success_rate REAL,                -- 成功 tool call / 总 tool call
    termination_reason TEXT,               -- 复用现有常量
    error_summary   TEXT,                  -- 出错时的简要描述
    git_commit      TEXT,                  -- 运行时的 HEAD commit
    config_json     TEXT,                  -- JSON 序列化的 experiment_config
    created_at      REAL NOT NULL          -- 行创建时间（提交时写入）
);
```

**状态机转移：**

```
pending → running      （loop 进入时）
running → success      （termination_reason = verification_passed）
running → failed       （其余终止原因）
running → cancelled    （收到取消信号）
running → timeout      （超过全局 wall-clock 限制）
```

中断（进程崩溃）的 run 在 `runs` 表中保持 `running` 状态。resume 时检测到此状态后从最近 checkpoint 恢复。

### 3.2 RunStepRecord（run_steps 表）

每个 ReAct step（思考 + 工具调用 + 观测）对应一条记录。

```sql
CREATE TABLE run_steps (
    step_pk         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    step_index      INTEGER NOT NULL,      -- 0-based，对应 LoopState.steps
    thought_text    TEXT,                  -- LLM 输出的思考部分（截断至 2000 字符）
    observation_text TEXT,                 -- 本 step 的观测摘要（截断至 2000 字符）
    tool_call_count INTEGER DEFAULT 0,     -- 本 step 调用的 tool 数量
    had_error       INTEGER DEFAULT 0,     -- 是否有 tool 返回 is_error=True
    step_tokens     INTEGER DEFAULT 0,     -- 本 step 消耗的 token 数
    step_duration_ms INTEGER DEFAULT 0,    -- 本 step 耗时（ms）
    loop_state_json TEXT,                  -- LoopState 关键字段的 JSON 快照（见 3.4）
    recorded_at     REAL NOT NULL
);
CREATE INDEX idx_run_steps_run_id ON run_steps(run_id, step_index);
```

### 3.3 ToolCallRecord（tool_calls 表）

每次 tool call 对应一条独立记录，与 step 关联。

```sql
CREATE TABLE tool_calls (
    call_pk         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    step_index      INTEGER NOT NULL,
    tool_use_id     TEXT NOT NULL,         -- 对应 Anthropic/OpenAI tool_use_id
    tool_name       TEXT NOT NULL,         -- read_file / write_file / run_command 等
    args_json       TEXT,                  -- 调用参数（JSON，敏感内容可裁剪）
    result_text     TEXT,                  -- 输出结果（截断至 4000 字符）
    is_error        INTEGER DEFAULT 0,
    error_kind      TEXT,                  -- tool_error / unknown_tool / tool_execution_error
    duration_ms     INTEGER DEFAULT 0,
    recorded_at     REAL NOT NULL
);
CREATE INDEX idx_tool_calls_run_id ON tool_calls(run_id, step_index);
```

### 3.4 LoopState 快照格式

每个 step 后将 `LoopState` 的关键字段序列化为 JSON 存入 `loop_state_json`，用于 resume 时恢复状态。

```python
# 只序列化可安全重建的字段；MessageHistory 从 run_steps 重建
{
    "steps": int,
    "retry_count": int,
    "retry_steps": int,
    "last_error_type": str | None,
    "last_error_signature": str | None,
    "verification_attempts": int,
    "verification_failures": int,
    "consecutive_verification_failures": int,
    "recovery_mode": str,
    "retry_edit_target": str | None,
    "consecutive_identical_failures": int,
    "doom_loop_warnings_injected": int,
    "observations_compressed": int,
    "compaction_events": int,
    "ad_hoc_install_count": int,
    "tried_approaches": list[dict],
    "approach_memory_injections": int,
    "cross_task_memory_injected": bool,
    "memory_injections": int,
}
```

---

## 4. 持久化 Run State 实现

### 4.1 新文件：`coder_agent/memory/run_state.py`

新增 `RunStateStore` 类，独立管理 SQLite 文件（默认路径 `~/.coder_agent/run_state.db`，可通过 `config.yaml` 的 `agent.run_state_db_path` 覆盖）。

```python
class RunStateStore:
    def __init__(self, db_path: Path): ...

    # --- Run 生命周期 ---
    def create_run(self, run_id: str, task_description: str,
                   experiment_id: str, **kwargs) -> None:
        """INSERT run with status='pending'"""

    def start_run(self, run_id: str) -> None:
        """UPDATE status='running', started_at=now()"""

    def finish_run(self, run_id: str, status: str,
                   termination_reason: str | None,
                   error_summary: str | None,
                   metrics: RunMetrics) -> None:
        """UPDATE status, finished_at, metrics"""

    def cancel_run(self, run_id: str) -> bool:
        """UPDATE status='cancelled'，返回是否成功（仅 running 状态可取消）"""

    # --- Step checkpoint ---
    def record_step(self, run_id: str, step_index: int,
                    thought: str, observation: str,
                    tool_call_count: int, had_error: bool,
                    step_tokens: int, step_duration_ms: int,
                    loop_state: LoopState) -> None:
        """INSERT run_steps，序列化 loop_state_json"""

    # --- Tool call audit ---
    def record_tool_call(self, run_id: str, step_index: int,
                         tool_use_id: str, tool_name: str,
                         args: dict, result: str,
                         is_error: bool, error_kind: str | None,
                         duration_ms: int) -> None:
        """INSERT tool_calls"""

    # --- 查询 ---
    def get_run(self, run_id: str) -> dict | None: ...
    def get_run_steps(self, run_id: str) -> list[dict]: ...
    def get_tool_calls(self, run_id: str) -> list[dict]: ...
    def list_runs(self, limit: int = 20,
                  status: str | None = None) -> list[dict]: ...

    # --- Resume 支持 ---
    def get_latest_checkpoint(self, run_id: str) -> dict | None:
        """返回最后一条 run_steps 行（含 loop_state_json），None 表示尚未有 step"""

    def get_interrupted_runs(self) -> list[dict]:
        """返回 status='running' 的 run 列表（可能是崩溃遗留）"""

    def close(self) -> None: ...
```

**关键设计约束：**
- 所有写操作使用 `executemany` + `BEGIN IMMEDIATE` 事务，避免 WAL 竞争
- `loop_state_json` 写入前先 `json.dumps` + 长度截断（上限 64KB）
- `result_text` 超过 4000 字符时截断末尾并追加 `"...[truncated]"`

### 4.2 修改：`coder_agent/core/agent_loop.py`

#### 4.2.1 函数签名变更

```python
# 当前
async def run_agent_loop(agent, user_input, ...) -> TurnResult

# v0.7.2（新增 run_state_store 和 run_id 参数）
async def run_agent_loop(
    agent,
    user_input: str,
    run_id: str,                              # 新增：由 agent.run() 生成
    run_state_store: RunStateStore | None,    # 新增：None 则跳过持久化（向后兼容）
    ...
) -> TurnResult
```

`run_state_store=None` 时行为与 v0.7.1 完全一致，不引入任何额外开销。

#### 4.2.2 checkpoint 写入点

在主循环每次迭代末尾，`_update_failure_tracking()` 之后，插入：

```python
# agent_loop.py 主循环末尾（伪代码）
if run_state_store is not None:
    step_end_time = time.monotonic()
    await asyncio.to_thread(
        run_state_store.record_step,
        run_id=run_id,
        step_index=state.steps,
        thought=_extract_thought(turn),           # 截断至 2000 字符
        observation=batch.combined_observation,   # 截断至 2000 字符
        tool_call_count=len(turn.tool_uses),
        had_error=batch.detected_error,
        step_tokens=_count_step_tokens(turn, batch),
        step_duration_ms=int((step_end_time - step_start_time) * 1000),
        loop_state=state,
    )
```

tool call 写入点在 `execute_tools()` 返回后、`ToolBatchSummary` 构建前：

```python
if run_state_store is not None:
    for call, result in zip(turn.tool_uses, tool_results):
        await asyncio.to_thread(
            run_state_store.record_tool_call,
            run_id=run_id,
            step_index=state.steps,
            tool_use_id=call["id"],
            tool_name=call["name"],
            args=call.get("input", {}),
            result=result.get("content", ""),
            is_error=result.get("is_error", False),
            error_kind=result.get("error_kind"),
            duration_ms=result.get("_duration_ms", 0),   # execute.py 新增计时
        )
```

**写入频率**：每 step 写一次 `run_steps`，每 tool call 写一次 `tool_calls`。不做批量延迟写入，因为目标是崩溃可恢复。

#### 4.2.3 resume 入口

在 `run_agent_loop()` 函数头部、第一次 LLM 调用之前，新增恢复逻辑：

```python
resume_from_step: int = 0
if run_state_store is not None:
    checkpoint = run_state_store.get_latest_checkpoint(run_id)
    if checkpoint is not None:
        # 从上次 step 恢复 LoopState
        state = _restore_loop_state(checkpoint["loop_state_json"], state)
        resume_from_step = checkpoint["step_index"] + 1
        # 重建 MessageHistory：从 run_steps 取 thought/observation 对
        _rebuild_message_history(agent.history, run_state_store, run_id)
```

`_restore_loop_state(json_str, base_state) -> LoopState`：反序列化 JSON，用恢复的字段覆盖 `base_state` 的对应字段。

`_rebuild_message_history(history, store, run_id)`：从 `run_steps` 加载历史 thought/observation，重建精简版 `MessageHistory`（只保留摘要，不保留原始 LLM XML）。这已满足 doom-loop 检测和 approach memory 注入的需要。

### 4.3 修改：`coder_agent/core/agent.py`

#### 4.3.1 新增 run_id 生成和 RunStateStore 注入

```python
import uuid

class Agent:
    def __init__(self, ..., run_state_store: RunStateStore | None = None):
        ...
        self._run_state_store = run_state_store

    def run(self, user_input: str, ...) -> TurnResult:
        run_id = str(uuid.uuid4())        # 每次 run() 生成新 UUID
        if self._run_state_store:
            self._run_state_store.create_run(
                run_id=run_id,
                task_description=user_input,
                experiment_id=self.experiment_id,
                preset=self.experiment_config.get("preset"),
                llm_profile=self._model_cfg.name,
                workspace_path=str(self.workspace),
                git_commit=_get_git_commit(),
                config_json=json.dumps(self.experiment_config),
            )
        result = asyncio.run(self._run_with_cleanup(
            user_input, run_id=run_id, ...
        ))
        return result
```

`TurnResult` 已有 `trajectory_id` 字段；新增 `run_id: str | None = None` 字段，供调用方拿到持久化 ID。

#### 4.3.2 工厂函数 `make_agent()`

新增模块级工厂函数，统一 CLI 和 API 的 agent 构建路径：

```python
# coder_agent/core/agent.py
def make_agent(
    preset: str | None = None,
    llm_profile_name: str | None = None,
    workspace: Path | None = None,
    enable_memory: bool = True,
    enable_run_state: bool = True,       # 控制是否启用 RunStateStore
) -> Agent:
    """统一的 agent 构建入口，返回配置好的 Agent 实例。"""
    ...
```

CLI 的 `run` 命令和 API 的 `POST /runs` handler 均调用此函数，消除现有 CLI 中重复的 agent 构建逻辑。

### 4.4 修改：`coder_agent/config.py`

`AgentConfig` 新增字段：

```python
@dataclass
class AgentConfig:
    ...
    run_state_db_path: Path = Path("~/.coder_agent/run_state.db")
    enable_run_state: bool = True           # False 退化到 v0.7.1 行为
    run_state_step_truncation: int = 2000   # thought/observation 截断长度
    run_state_result_truncation: int = 4000 # tool result 截断长度
```

`config.yaml` 对应新增：

```yaml
agent:
  ...
  run_state_db_path: ~/.coder_agent/run_state.db
  enable_run_state: true
```

---

## 5. 服务化 API 层实现

### 5.1 新文件结构

```
coder_agent/
  service/
    __init__.py
    app.py          # FastAPI application + lifespan
    models.py       # Pydantic request/response 模型
    runner.py       # 后台任务执行逻辑
```

### 5.2 API 端点规格

#### `POST /runs` — 提交任务

**Request body：**

```json
{
  "task": "Fix the failing test in src/utils.py",
  "preset": "C3",
  "llm_profile": "minimax_m27",
  "workspace": "/path/to/repo",
  "max_steps": 15,
  "metadata": {}
}
```

字段说明：
- `task`（必填）：任务描述，对应 `user_input`
- `preset`（可选，默认 `default`）：agent preset，对应 `experiment_id`
- `llm_profile`（可选）：LLM profile 名称，覆盖 config.yaml 默认值
- `workspace`（可选）：工作目录，默认使用 `cfg.agent.workspace`
- `max_steps`（可选，默认 15）：最大 step 数
- `metadata`（可选）：透传到 `run.config_json`，供后续平台查询使用

**Response（202 Accepted）：**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "created_at": 1744185600.0
}
```

**实现：** handler 调用 `make_agent()`，将 `agent.run()` 提交给 `BackgroundTasks`，立即返回 `run_id`。

#### `GET /runs/{run_id}` — 查询 run 状态

**Response（200）：**

```json
{
  "run_id": "550e8400-...",
  "status": "running",
  "task": "Fix the failing test...",
  "preset": "C3",
  "llm_profile": "minimax_m27",
  "started_at": 1744185601.2,
  "finished_at": null,
  "total_steps": 7,
  "total_tool_calls": 12,
  "total_tokens": 34210,
  "tool_success_rate": 0.917,
  "termination_reason": null,
  "error_summary": null
}
```

**错误（404）：** `{"detail": "run not found"}`

#### `GET /runs/{run_id}/steps` — 查询 step 列表

**Response（200）：**

```json
{
  "run_id": "550e8400-...",
  "steps": [
    {
      "step_index": 0,
      "thought_text": "I need to first read the failing test file...",
      "observation_text": "File content: ...",
      "tool_call_count": 1,
      "had_error": false,
      "step_tokens": 1820,
      "step_duration_ms": 3241,
      "recorded_at": 1744185604.5
    }
  ]
}
```

#### `POST /runs/{run_id}/cancel` — 取消 run

**Response（200）：**

```json
{"run_id": "550e8400-...", "cancelled": true}
```

取消机制：在 `runner.py` 中为每个 run 维护一个 `asyncio.Event`（存于全局 dict，key 为 `run_id`）。`run_agent_loop()` 在每个 step 开始前检查此 event，若已设置则抛出 `RunCancelledError`，loop 捕获后将 status 设为 `cancelled`。

**Response（409 Conflict）：** run 已完成，无法取消

```json
{"detail": "run already finished with status: success"}
```

#### `GET /runs` — 列出 run（可选筛选）

**Query params：** `status`, `limit`（默认 20）, `offset`（默认 0）

**Response（200）：**

```json
{
  "runs": [...],
  "total": 42
}
```

#### `GET /health` — 健康检查

```json
{"status": "ok", "version": "0.7.2"}
```

### 5.3 新文件：`coder_agent/service/models.py`

```python
from pydantic import BaseModel, Field
from typing import Literal

class SubmitRunRequest(BaseModel):
    task: str
    preset: str = "default"
    llm_profile: str | None = None
    workspace: str | None = None
    max_steps: int = Field(default=15, ge=1, le=50)
    metadata: dict = Field(default_factory=dict)

class RunResponse(BaseModel):
    run_id: str
    status: Literal["pending", "running", "success", "failed",
                    "cancelled", "timeout"]
    task: str
    preset: str | None
    llm_profile: str | None
    started_at: float | None
    finished_at: float | None
    total_steps: int
    total_tool_calls: int
    total_tokens: int
    tool_success_rate: float | None
    termination_reason: str | None
    error_summary: str | None

class StepResponse(BaseModel):
    step_index: int
    thought_text: str | None
    observation_text: str | None
    tool_call_count: int
    had_error: bool
    step_tokens: int
    step_duration_ms: int
    recorded_at: float

class RunStepsResponse(BaseModel):
    run_id: str
    steps: list[StepResponse]
```

### 5.4 新文件：`coder_agent/service/app.py`

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException

# Global state（仅供单进程使用）
_run_state_store: RunStateStore | None = None
_cancel_events: dict[str, asyncio.Event] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _run_state_store
    db_path = Path(cfg.agent.run_state_db_path).expanduser()
    _run_state_store = RunStateStore(db_path)
    yield
    if _run_state_store:
        _run_state_store.close()

app = FastAPI(title="Coder Agent Service", version="0.7.2", lifespan=lifespan)

@app.post("/runs", status_code=202)
async def submit_run(req: SubmitRunRequest, background: BackgroundTasks):
    run_id = str(uuid.uuid4())
    cancel_event = asyncio.Event()
    _cancel_events[run_id] = cancel_event
    # 创建 run record（pending 状态）
    _run_state_store.create_run(run_id=run_id, task_description=req.task, ...)
    # 构建 agent 并提交后台任务
    agent = make_agent(preset=req.preset, llm_profile_name=req.llm_profile,
                       workspace=Path(req.workspace) if req.workspace else None,
                       enable_run_state=True)
    agent._run_state_store = _run_state_store
    background.add_task(_execute_run, agent, run_id, req.task,
                        req.max_steps, cancel_event)
    return {"run_id": run_id, "status": "pending",
            "created_at": time.time()}
```

### 5.5 新增 CLI 命令：`coder-agent serve`

在 `cli/main.py` 新增 `serve` 子命令：

```python
@cli.command("serve")
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8765, type=int)
@click.option("--reload", is_flag=True, default=False,
              help="开发模式，文件变化时自动重载")
def serve_command(host: str, port: int, reload: bool):
    """启动 HTTP 服务，通过 REST API 提交和查询 agent run。"""
    import uvicorn
    uvicorn.run("coder_agent.service.app:app",
                host=host, port=port, reload=reload)
```

### 5.6 新增依赖

在 `pyproject.toml` 新增：

```toml
[project]
dependencies = [
    ...
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
]
```

---

## 6. Resume 命令（CLI）

在 `cli/main.py` 的 `run` 子命令新增 `--resume` 选项：

```python
@cli.command("run")
@click.option("--resume", default=None, metavar="RUN_ID",
              help="从已有 run 的最近 checkpoint 恢复执行")
def run_command(task, ..., resume: str | None):
    if resume:
        # 从 RunStateStore 加载原始 run 信息
        store = RunStateStore(db_path)
        run_info = store.get_run(resume)
        if run_info is None:
            raise click.ClickException(f"run not found: {resume}")
        if run_info["status"] not in ("running", "failed"):
            raise click.ClickException(
                f"run {resume} has status '{run_info['status']}', cannot resume"
            )
        # 用原始 run 的 task/preset/workspace 重建 agent，传入 run_id
        agent = make_agent(preset=run_info["preset"], ...)
        result = agent.run(run_info["task_description"], run_id=resume)
    else:
        agent = make_agent(...)
        result = agent.run(task)
```

`agent.run()` 在收到已有 `run_id` 时，跳过 `create_run()`，直接调用 `start_run()` 并触发 resume 逻辑。

---

## 7. 向后兼容性保证

| 场景 | 行为 |
|------|------|
| `enable_run_state: false`（config.yaml）| `RunStateStore` 不创建，`run_state_store=None` 传入 loop，行为与 v0.7.1 完全一致 |
| 无 `run_state_db_path` 配置 | 使用默认路径 `~/.coder_agent/run_state.db`，自动创建 |
| eval runner（`EvalRunner.run_suite`）| 默认 `enable_run_state=True`，每个 task run 均产生 `RunRecord` |
| `TrajectoryStore` | 不修改，JSONL 写入逻辑不变 |
| `MemoryManager` | 不修改 schema，`task_history` 写入时机不变 |
| `TurnResult` | 新增 `run_id: str | None = None` 字段（有默认值，不破坏现有用法）|

---

## 8. 测试策略

### 8.1 新增测试文件

| 文件 | 内容 |
|------|------|
| `tests/test_run_state_store.py` | `RunStateStore` 的 CRUD、状态转移、checkpoint 读写 |
| `tests/test_run_state_resume.py` | `_restore_loop_state()` 和 `_rebuild_message_history()` 的正确性 |
| `tests/test_service_api.py` | FastAPI 端点的 happy path 和错误路径（使用 `httpx.AsyncClient` + `TestClient`）|
| `tests/test_cancel.py` | cancel event 传播，run status 从 running 变为 cancelled |

### 8.2 测试覆盖要求

- `RunStateStore`：
  - `create_run` → `start_run` → `finish_run` 状态转移正确
  - `record_step` 写入后 `get_latest_checkpoint` 返回最新行
  - `loop_state_json` 序列化/反序列化 round-trip 无损（针对 `tried_approaches` 嵌套列表）
  - DB 路径不存在时自动创建父目录

- Resume：
  - 模拟 3 个 step 后"崩溃"（直接丢弃 agent），新建 agent + resume，验证 `LoopState.steps` 从 3 开始
  - `_rebuild_message_history` 后 MessageHistory token count 可估算（不为 0）

- API：
  - `POST /runs` 返回 202，`run_id` 为 UUID 格式
  - `GET /runs/{run_id}` 在 run 完成前返回 `status: running`，完成后返回 `status: success/failed`
  - `POST /runs/{run_id}/cancel` 对已完成 run 返回 409
  - `GET /runs/nonexistent` 返回 404

### 8.3 不需要新增测试的部分

- `TrajectoryStore`（不变）
- `MemoryManager`（不变）
- 已有 agent_loop 逻辑（已有 `test_agent_termination.py` 等覆盖）

---

## 9. 实现阶段拆分

### Phase A：RunStateStore + checkpoint（先做，不依赖 API）

1. 实现 `coder_agent/memory/run_state.py`（`RunStateStore` 类，含 schema 初始化）
2. `coder_agent/config.py`：新增 `run_state_db_path`、`enable_run_state` 字段
3. `coder_agent/core/agent_loop.py`：新增 `run_id` + `run_state_store` 参数，插入 step/tool_call 写入点
4. `coder_agent/core/agent.py`：新增 `run_id` 生成、`RunStateStore` 注入、`make_agent()` 工厂
5. `tests/test_run_state_store.py` + `tests/test_run_state_resume.py`

完成标志：运行 `coder-agent run "some task"`，`~/.coder_agent/run_state.db` 中有对应 `runs` + `run_steps` + `tool_calls` 记录。

### Phase B：resume CLI

1. `cli/main.py`：`run` 命令新增 `--resume` 选项
2. `agent_loop.py`：实现 `_restore_loop_state()` 和 `_rebuild_message_history()`
3. `tests/test_run_state_resume.py`：resume 路径覆盖

完成标志：`coder-agent run --resume <run_id>` 能从中断 run 的最后 step 继续执行。

### Phase C：FastAPI 服务层

1. `coder_agent/service/models.py`（Pydantic 模型）
2. `coder_agent/service/runner.py`（后台执行 + cancel event 管理）
3. `coder_agent/service/app.py`（FastAPI app，lifespan，5 个端点）
4. `cli/main.py`：`serve` 子命令
5. `pyproject.toml`：新增 `fastapi`、`uvicorn` 依赖
6. `tests/test_service_api.py` + `tests/test_cancel.py`

完成标志：`coder-agent serve` 启动后，`curl -X POST localhost:8765/runs -d '{"task":"..."}'` 返回 `run_id`，`curl localhost:8765/runs/{run_id}` 能查到最终状态。

---

## 10. 版本发布条件

### 必须满足（否则不合并）

- [ ] Phase A、B、C 均完成
- [ ] 所有新增测试通过（`pytest tests/test_run_state_store.py tests/test_run_state_resume.py tests/test_service_api.py tests/test_cancel.py`）
- [ ] 现有测试套件无回归（`pytest tests/` 全绿，除已知 skip）
- [ ] SWE promoted C3/C6 baseline 复跑结果不低于 2/8（验证 enable_run_state 未影响 agent 行为）
- [ ] `enable_run_state: false` 的回归测试：Custom benchmark 结果不变

### 推荐（可在后续 patch 补）

- [ ] `GET /runs/{run_id}/steps` 端点返回完整 step 列表
- [ ] `coder-agent runs list` CLI 命令（打印最近 N 条 run 记录）
- [ ] run_state.db 的 schema migration 机制（版本字段 + ALTER TABLE）
- [ ] SIGTERM 信号捕获：服务收到 SIGTERM 后将所有 running run 标记为 cancelled 而非留在 running 状态

---

## 11. 文件变更清单

| 操作 | 文件 |
|------|------|
| **新建** | `coder_agent/memory/run_state.py` |
| **新建** | `coder_agent/service/__init__.py` |
| **新建** | `coder_agent/service/models.py` |
| **新建** | `coder_agent/service/runner.py` |
| **新建** | `coder_agent/service/app.py` |
| **新建** | `tests/test_run_state_store.py` |
| **新建** | `tests/test_run_state_resume.py` |
| **新建** | `tests/test_service_api.py` |
| **新建** | `tests/test_cancel.py` |
| **修改** | `coder_agent/core/agent.py`（run_id 生成、RunStateStore 注入、make_agent 工厂）|
| **修改** | `coder_agent/core/agent_loop.py`（run_id/run_state_store 参数、step/tool_call 写入点、resume 逻辑）|
| **修改** | `coder_agent/config.py`（AgentConfig 新增字段）|
| **修改** | `coder_agent/cli/main.py`（`run --resume` 选项、`serve` 子命令）|
| **修改** | `config.yaml`（新增 run_state 相关配置项）|
| **修改** | `pyproject.toml`（新增 fastapi、uvicorn 依赖，版本号 0.7.1 → 0.7.2）|

共新建 9 个文件，修改 6 个文件。`TrajectoryStore`、`MemoryManager`、`EvalRunner` 及所有现有 benchmark loader 无需修改。

---

## 12. 与路线图的对应关系

| 路线图要求（Phase 1） | v0.7.2 实现 |
|----------------------|------------|
| 持久化 run state | `RunStateStore`，每 step checkpoint |
| pause / resume / retry | `--resume <run_id>` CLI，从 checkpoint 恢复 |
| tool trace 存储 | `tool_calls` 表，每次 tool call 独立记录 |
| 支持任务超时和取消 | cancel event + `POST /runs/{id}/cancel` |
| API 服务层（异步提交）| `coder-agent serve` + `POST /runs` |
| run status 查询 | `GET /runs/{run_id}` |
| verification result 记录 | 通过 `termination_reason` 字段复用（`verification_passed` / `verification_failed`）|

路线图 Phase 1 中"输出第一版 agent runtime 架构图"和"API 服务层完整文档"推迟到 v0.7.2 合并后单独整理，不作为代码合并的阻塞项。
