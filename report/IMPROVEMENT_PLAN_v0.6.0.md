# Improvement Plan v0.6.0 — Runtime 基础设施 + SWE-bench 引入

**Date:** 2026-04-07 (rev 3 — scope update post Codex review)  
**Branch:** (TBD)  
**Baseline:** v0.5.2 — 多 profile 系统已上线；0.5.1 accepted baseline: C4=90%, C6=90%

---

## 0. 背景与动机

当前 `0.6.0` 不能只看作 “eval runner 小修补”。现状同时存在 runtime 正确性问题和 benchmark 判别力问题，二者都需要进入本版本范围。

### 问题 A — Workspace 共享（高优先级）

`runner.py` 当前在 `run_task()` 内硬编码使用全局 `cfg.agent.workspace`。`prepare_workspace()` 会在每个任务开始时破坏性清理该目录。顺序执行尚可工作，但存在以下问题：

- 同一 `run_suite()` 内，上一个任务的文件在清理前仍存在，有潜在污染
- 用同一 `config_label` 跑两次，第二次会静默覆盖第一次的 workspace 内容
- `compare_configs()` 串行调用多个 `run_suite()` 共享全局 workspace；一旦并行化会立即互相踩踏

### 问题 A2 — Workspace 只在 runner 层改还不够

即使 `run_task()` 改成 per-run workspace，当前 agent 运行时仍有多处直接绑定全局 workspace：

- `tools/file_tools.py`、`tools/shell_tool.py`、`tools/search_tool.py` 在模块导入时就缓存 `_WORKSPACE = cfg.agent.workspace`
- `core/agent.py` 的 memory project 记录直接使用 `cfg.agent.workspace`
- `core/session.py`、`core/agent_prompt.py`、`core/agent_errors.py` 也直接读取全局 workspace

如果只修改 eval runner 而不把 workspace 继续传递到 agent / tool / memory / prompt 层，那么 “workspace 隔离” 只对 `prepare_workspace()` 和 verification 生效，对 agent 实际读写和执行路径并不成立。

### 问题 B — Resume 语义不完整，且一致性校验过弱

当前 `--resume` 仅重载已完成任务的 checkpoint 结果（从 `.jsonl` 文件），不恢复：

- workspace 文件状态
- agent 对话历史
- loop 状态

这意味着当前 resume 的真实语义是：

> 跳过已完成任务，重新开始未完成任务。

这个语义本身可以接受，但必须显式文档化，并且需要校验：

- `benchmark`
- `preset` / agent config snapshot
- runtime experiment config snapshot
- `llm_profile`
- task ID 集合
- `run_id` / `workspace_path`

否则用户可以在不同运行配置之间错误地拼接结果，破坏 artifact 审计性。

### 问题 C — 现有测试集不足以稳定区分配置能力差异

当前 Custom 40-task suite 足够做日常回归，但对配置间能力差异的区分度不够稳定：

- 很多任务过短、过窄，容易被 stop discipline 或一次性启发式掩盖
- 对跨文件修改、真实仓库上下文、补丁质量的区分能力不足
- 不同 preset 之间的差距有时更多体现在运气、provider 波动或单题偶然性，而非真实能力边界

因此 `0.6.0` 需要及时引入一个更接近真实修复工作负载的基准，优先采用 version-pinned 的 SWE-bench 子集，而不是继续只依赖现有 Custom 套件。

### 问题 D — HTTP API 前置条件仍不满足（后置）

在 workspace 隔离、持久 run 标识和稳定 benchmark artifact 契约建立前，HTTP API 层缺乏安全基础：

- 无全局 `run_id -> workspace` 反查索引
- 无跨进程持久 run state store
- 无取消语义

因此 API 层仍应后置，但不应挤占 `0.6.0` 对 runtime 正确性和 benchmark 升级的主线。

---

## 1. 范围和版本拆分

- **v0.6.0a（必做）**：Runtime 正确性基础
  - per-run workspace 隔离
  - workspace 传递到 agent / tool / memory / prompt 全链路
  - 唯一 `run_id`
  - manifest run 元数据
  - resume 语义文档化与一致性校验
- **v0.6.0b（必做）**：SWE-bench 引入
  - 增加 `swebench` benchmark 入口
  - 固定任务子集与 artifact 契约
  - 用于配置区分和后续 rebaseline 辅助分析
- **v0.6.0c（可选，后置）**：Job manager + HTTP API

本文档详细规划 `v0.6.0a` 和 `v0.6.0b`。`v0.6.0c` 仅保留前置条件，不在本轮实现范围内。

---

## 2. v0.6.0a — Runtime 正确性基础

### Change 1：`run_task()` 通过 helper 统一解析 workspace

目标：`run_task()` 不再直接读取 `cfg.agent.workspace`，而是显式使用当前 run 的 workspace。

```python
def run_task(
    self,
    task: TaskSpec,
    agent: Any,
    config_label: str = "",
    workspace: Path | None = None,
    run_id: str | None = None,
) -> EvalResult:
    if workspace is None:
        workspace = self._resolve_run_workspace(config_label, run_id)
    prepare_workspace(task.setup_files, workspace)
    verification_hook = build_verification_hook(task, workspace)
    ...
```

要求：

- `prepare_workspace()`、`build_verification_hook()`、`run_custom_checks()`、`run_humaneval_check()`、`run_mbpp_check()` 全部使用同一个已解析的 `workspace`
- `tests/test_eval_runner.py` 中对 `run_task()` 的直接调用保留向后兼容默认值，但新增覆盖 per-run workspace 的测试

### Change 2：正式引入 `_allocate_run_id()`，禁止秒级时间戳碰撞方案

计划不再接受“秒级 Unix 时间戳即可”的宽松表述。`run_id` 必须由单独 helper 生成，并满足：

- 单进程内重复调用不会碰撞
- 同秒内重复运行不会碰撞
- 字符串可直接作为路径片段使用

推荐格式：

- `YYYYMMDDHHMMSS-<8hex>`，或
- `uuid.uuid4().hex[:12]`

计划要求：

- 生成逻辑收敛到一个 helper，例如 `_allocate_run_id()`
- `_resolve_run_workspace()` 只负责路径拼接，不再隐式生成 `run_id`
- 不再出现从路径对象“反推 run_id”的伪逻辑，也不再使用无效占位写法

```python
def _allocate_run_id(self) -> str:
    ...

def _resolve_run_workspace(self, config_label: str, run_id: str | None = None) -> Path:
    base = cfg.agent.workspace
    if not config_label:
        return base
    if run_id is None:
        raise ValueError("run_id is required for labeled eval runs")
    return base / config_label / run_id
```

### Change 3：`run_suite()` 统一管理 `run_id` 生命周期

目标：`run_id` 在 `run_suite()` 开头确定，并贯穿整次 run。

要求：

- 非 resume：
  - 先调用 `_allocate_run_id()`
  - 再解析 `run_workspace`
  - 清理旧 artifacts
- resume：
  - 从旧 manifest 读取 `run_id`
  - 复用同一 `run_workspace`
  - 若 manifest 缺少 `run_id` 或 `workspace_path`，视为 legacy manifest

关于 legacy manifest：

- `v0.6.0` 之前生成的 manifest 不具备 per-run workspace 语义
- 对 legacy manifest 的 `--resume` 不再静默兼容
- 计划采用 fail-fast：提示用户该 run 不能按新契约恢复，请重新发起 fresh run

原因：旧格式 resume 若继续允许，会把新旧语义混在一起，破坏审计性。

### Change 4：把 workspace 继续传递到 agent / tool / memory / prompt 全链路

这是本次 rev 3 新增的必做项。

#### 4.1 Agent runtime 持有显式 workspace

`Agent` 新增 runtime workspace 概念，例如：

```python
Agent(..., workspace: Path)
```

要求：

- `Agent` 实例持有 `self.workspace`
- `reset()` 不丢失该 workspace
- memory project 记录使用 `self.workspace`，不再使用全局 `cfg.agent.workspace`

#### 4.2 Tool registry 改为按 workspace 构建

当前 `build_tools()` 是无参工厂，tool 模块在导入时缓存 workspace。需要改成：

```python
build_tools(workspace: Path) -> list[Tool]
```

要求：

- `ReadFileTool` / `WriteFileTool` / `ListDirTool`
- `RunCommandTool`
- `SearchCodeTool`

均在实例级别接收 workspace，而不是依赖模块级 `_WORKSPACE`

#### 4.3 Prompt / session / error guidance 同步使用 agent workspace

需要同步改造：

- `core/agent_prompt.py`
- `core/session.py`
- `core/agent_errors.py`

要求：

- 系统提示中的 workspace 路径与当前 run 一致
- session metadata 反映当前 workspace
- import error guidance 中的本地文件搜索基于当前 agent workspace

#### 4.4 factory.py 调用链更新（必做，否则 Change 4 断链）

当前 `make_agent()` 在 `cli/factory.py:L71` 调用 `build_tools()`（无参），不接收 workspace：

```python
# 当前：factory.py:L71
agent = Agent(tools=build_tools(), ...)
```

Change 4 要求 `make_agent()` 新增 `workspace: Path | None = None` 参数：

```python
def make_agent(
    agent_config: dict | None = None,
    *,
    workspace: Path | None = None,    # ← 新增
    ...
) -> Agent:
    resolved_workspace = workspace or cfg.agent.workspace
    agent = Agent(
        tools=build_tools(resolved_workspace),
        ...
    )
```

当前 `EvalRunner` 持有 `agent_factory: Callable[[dict], Any]`，在 `run_suite()` 中调用 `self.agent_factory(agent_config or {})`。此 callable 签名需同步更新为 `Callable[[dict, Path], Any]`，由 eval CLI 在构建 factory 时闭包捕获：

```python
# cli/eval.py 中构建 factory 的方式
def agent_factory(agent_config: dict, workspace: Path) -> Agent:
    return make_agent(agent_config, workspace=workspace, ...)

runner = EvalRunner(agent_factory=agent_factory, ...)
```

`run_suite()` 内改为：

```python
agent = self.agent_factory(agent_config or {}, run_workspace)
```

**chat 模式的处理：** `make_session()` 也调用 `make_agent()`，但 chat 模式不参与 per-run workspace 隔离（见第 7 节排除范围）。`make_session()` 不传 workspace 参数，默认使用 `cfg.agent.workspace`，向后兼容。这意味着 chat 模式下工具仍绑定全局 workspace，这在本版本范围内是可接受的行为。

### Change 5：manifest 记录 run 元数据，并澄清 “Run 索引” 定义

`write_run_manifest()` 新增字段：

```json
{
  "run_id": "20260407153000-a1b2c3d4",
  "workspace_path": "/abs/path/to/workspace/c4_m1_final/20260407153000-a1b2c3d4",
  "workspace_mode": "per_run_v1"
}
```

说明：

- `v0.6.0a` 中的 “Run 索引” 仅指 manifest 已具备 run 级身份信息
- **不** 包含全局 `run_id -> workspace` 查找表
- 真正的全局索引文件仍属于 `v0.6.0c`

这样可以避免 “计划标题写了 Run 索引，但实现里没有全局索引” 的术语歧义。

### Change 6：Resume 语义文档化 + 严格一致性校验

需要更新：

- `run_suite()` docstring
- CLI `--resume` help text
- `README.md` 的 Evaluation and Re-Baselining 章节
- `REBASELINE_PLAYBOOK_0_6_0.md`（新建）

文案基线：

> `--resume` 会从已有 checkpoint 恢复当前 eval run：跳过已完成任务，继续执行未完成任务。每个任务开始时 workspace 会从 setup_files 重建；不会恢复上一个任务结束时的文件状态、对话历史或 loop 状态。resume 仅允许在 benchmark、config snapshot、runtime experiment config、llm profile 和 task set 与原 run 一致时继续执行。

一致性校验策略：

- 硬失败：
  - `benchmark` 不一致
  - `preset` / `agent_config_sha256` 不一致
  - `runtime_experiment_config_sha256` 不一致
  - `llm_profile` / `llm_model` / `llm_transport` 不一致
  - task ID 集合不一致
  - manifest 缺失新契约要求的 `run_id` / `workspace_mode`
- 警告但允许继续：
  - `workspace_path` 绝对路径与当前解析路径不一致，但 `run_id` 一致

原因：绝对路径不一致可能来自工作目录迁移；配置或任务集不一致则属于真实的审计风险。

---

## 3. v0.6.0a — 验收条件

| # | 条件 | 验证方式 |
|---|------|---------|
| 1 | `uv run pytest` 全部通过，含新增 runtime workspace 测试 | `uv run pytest` |
| 2 | 有 `config_label` 的 `run_suite()` 调用，workspace 路径为 `<base>/<config_label>/<run_id>/` | 断言路径结构 |
| 3 | 同一 `config_label` 的两次非 resume 运行生成不同 `run_id` | 断言两次路径不同 |
| 4 | `run_id` 生成 helper 在高频调用下无碰撞 | 单元测试 / monkeypatch 时间源 |
| 5 | manifest 中写入 `run_id`、`workspace_path`、`workspace_mode` | 读取 manifest 断言 |
| 6 | `run_task()` 内部 `build_verification_hook(task, workspace)` 收到的 workspace 等于当前 run workspace | mock hook 捕获参数 |
| 7 | agent 工具执行实际工作目录等于当前 run workspace，而不是全局 `cfg.agent.workspace` | tool 测试 / agent 集成测试 |
| 8 | `file_tools.py`、`shell_tool.py`、`search_tool.py` 不再使用模块级固定 `_WORKSPACE` | 代码审查 / grep |
| 9 | memory project 记录使用当前 agent workspace | 单元测试 |
| 10 | 系统 prompt、session metadata、import error guidance 反映当前 workspace | 单元测试 |
| 11 | resume 场景下 `run_id` 从 manifest 读取，workspace 复用同一路径 | 构造 manifest 断言 |
| 12 | resume 若发现 config hash / llm profile / task set 不一致则硬失败 | `pytest.raises(...)` |
| 13 | resume 若使用 legacy manifest（缺少 `run_id` / `workspace_mode`）则硬失败 | `pytest.raises(...)` |
| 14 | `workspace_path` 绝对路径不一致时触发 warning，但不崩溃 | `pytest.warns(UserWarning)` |
| 15 | `--resume` CLI help text 已更新说明语义 | `uv run python -m coder_agent eval --help` |
| 16 | `README.md` 和 `REBASELINE_PLAYBOOK_0_6_0.md` 已同步新 resume 契约 | 文档检查 |
| 17 | Custom C6 smoke 回归：得分与 0.5.1 accepted baseline（90%）偏差 ≤ 5pp，确认 Change 4 工具链改动未引入能力回归 | `uv run python -m coder_agent eval --benchmark custom --preset C6 --limit 10` |

---

## 4. v0.6.0b — SWE-bench Lite 子集引入

### 4.1 目标

`v0.6.0b` 的目标不是追求 SWE-bench 公共榜单成绩，而是引入一个 **version-pinned 的官方 SWE-bench Lite 固定子集**，用真实的 `clone -> checkout -> diff -> test` 仓库修复流程作为 repository-repair 工作负载，提高配置间能力差异的评估分辨率。

当前仓库内的 swebench lane 采用官方 Lite 中筛选出的固定小子集，但每个任务仍需保留以下核心语义：
- 仓库级上下文 + 固定 `base_commit`（bug 存在的状态）
- 问题描述（issue text / task instruction）
- 通过/失败判定：能否让原来失败的测试变为通过，且不破坏原来通过的测试

### 4.2 范围

本轮使用 **version-pinned 的官方 Lite 固定子集**，而不是一次性运行更大规模的数据集。

建议分两层：

- `smoke` 子集：用于本地快速验证 runner / adapter / artifact 契约
- `promoted` 子集：用于配置差异比较和后续报告引用，任务列表固定并进入版本控制

要求：

- 任务列表固定并以“generated official manifest + local overrides”版本化保存
- 同一版本的比较必须使用完全相同的任务集合
- 不使用每次临时抽样的方式做配置比较
- **promoted 对比 preset：C3 vs C6**；C4 作为 experimental lane 单独标注，不纳入 promoted 结论（理由：C4 的 approach memory 来自 Custom 任务经验，注入到 repo-repair 任务语义错配，结论难以解释）

### 4.3 详细变更

#### Change 1：新增 `swebench` benchmark 入口

CLI 增加：

```bash
uv run python -m coder_agent eval --benchmark swebench ...
```

代码侧新增 benchmark 模块：

```text
coder_agent/eval/benchmarks/swebench/
  loader.py        # 从 generated official manifest + local overrides 加载任务列表，封装为 TaskSpec
  adapter.py       # workspace 准备：clone/checkout/diff/test
  official_manifest.generated.json  # checked-in official metadata snapshot
  local_overrides.json              # checked-in runtime override layer
```

#### Change 2：SWE-bench task workspace 采用 task 级子目录

SWE-bench 任务是仓库级上下文，不能沿用 Custom 的”单目录反复清理”心智模型。

计划要求：

- run 级 workspace 根目录仍为 `<base>/<config_label>/<run_id>/`
- 每个 SWE-bench task 在其下使用独立子目录：`<run_workspace>/<task_id>/`
- task 完成后保留该目录，便于失败分析和 patch 审计

这一步依赖 `v0.6.0a` 的 workspace 传递链路先完成。

**adapter.py 必须实现的 workspace 准备流程：**

Custom benchmark 的 `prepare_workspace()` 是 copy 文件。SWE-bench 的 `adapter.py` 需要：

1. `git clone <repo_url>` 到 `<run_workspace>/<task_id>/`（或从本地 mirror checkout）
2. `git checkout <base_commit>` 到 bug 存在的状态
3. agent 运行后，`git diff HEAD` 提取 patch artifact
4. 执行 task 指定的 test command（如 `pytest tests/`），判定通过/失败

运行环境要求：目标机器需有 git 和对应仓库的依赖环境（Python + 各仓库的依赖）。**v0.6.0b 不要求容器化隔离**，但建议在独立 virtualenv 中运行。

> **关于网络依赖：** 当前实现支持直接从 `repo_url` clone，也允许通过 `mirror_path` 使用本地缓存镜像来降低重复下载成本。

#### Change 3：引入 version-pinned task manifest

generated official manifest 记录：

- `dataset_name`
- `dataset_version`
- `source_mode`
- `subset`
- `tasks`：任务列表，每项包含：
  - `instance_id`：SWE-bench Lite 官方 task ID
  - `repo`：上游仓库标识
  - `repo_url`：真实仓库 URL
  - `mirror_path`：可选的本地 mirror 缓存路径
  - `base_commit`：checkout 目标
  - `test_command`：测试执行命令
  - `fail_to_pass`：需从失败变通过的测试列表
  - `pass_to_pass`：不得回归失败的测试列表

local overrides 记录：

- `subset`
- `mirror_path`
- `python_version`
- `setup_commands`
- `test_command_override`
- `expected_patch_targets`

要求：

- generated manifest 和 overrides 都进入版本控制，与代码同步
- 任何对 promoted 子集的变更都必须更新 `REBASELINE_PLAYBOOK_0_6_0.md`
- promoted 子集的任务应覆盖至少 3 个不同仓库，以降低单仓库偏差

**区分度要求（任务设计标准）：**

promoted 子集的任务应体现以下特征，以确保比 Custom suite 有更高配置区分度：

- 跨文件修改（agent 需要理解文件间依赖才能定位 bug）
- 有运行时错误需要 trace（不是静态读代码能发现的）
- 修复后必须通过已有测试，不能靠写新测试蒙混

#### Change 4：Verification 契约独立于 Custom checks

SWE-bench 不复用 Custom 的 `verification` 列表语义。判定逻辑：

- **通过**：`fail_to_pass` 中所有测试从失败变为通过，且 `pass_to_pass` 中无测试从通过变为失败
- **失败**：任一条件不满足
- `adapter.py` 负责执行测试、解析结果，返回标准 `EvalResult`

#### Change 5：将 SWE-bench 纳入配置区分工作流

`0.6.0` 之后的配置比较分两层解释：

- Custom suite：日常回归、功能烟测、低成本对比
- SWE-bench promoted 子集（C3 vs C6）：repository-repair 能力、跨文件修改、配置间真实能力差异

发布或报告时，不再只引用 Custom aggregate 指标来判断两个 preset 是否”等价”。

### 4.4 验收条件

| # | 条件 | 验证方式 |
|---|------|---------|
| 1 | CLI 支持 `--benchmark swebench` | `uv run python -m coder_agent eval --help` |
| 2 | SWE-bench smoke 子集在固定 task manifest 上跑通完整流程 | smoke run |
| 3 | 每个 task 使用独立 task workspace（`<run_workspace>/<task_id>/`） | 断言目录结构 |
| 4 | adapter 正确执行 clone → checkout → agent → diff → test 流程 | smoke run artifact 检查 |
| 5 | run manifest 记录 `dataset_name`、`source_mode`、`subset` 标签、task manifest 标识 | 读取 manifest |
| 6 | promoted 子集（C3 vs C6）产出固定命名的 artifact 与匹配 manifest | artifact 检查 |
| 7 | C4 在 SWE-bench 上的结果单独标注为 experimental，不写入 promoted 结论 | 报告检查 |
| 8 | `REBASELINE_PLAYBOOK_0_6_0.md` 明确 Custom 与 SWE-bench 的各自用途及 promoted preset 策略 | 文档检查 |

---

## 5. v0.6.0c — Job Manager + HTTP API（前置条件清单）

`v0.6.0c` **不在本计划执行范围内**，仅记录其前置条件。

| 前置条件 | 当前状态 | 满足于 |
|---------|---------|-------|
| per-run workspace 隔离（`config_label/run_id`） | ❌ 待实现 | v0.6.0a |
| workspace 传递到 agent / tool / memory 全链路 | ❌ 待实现 | v0.6.0a |
| manifest 记录 `run_id` 和 `workspace_path` | ❌ 待实现 | v0.6.0a |
| 固定 benchmark artifact 契约（含 SWE-bench subset） | ❌ 待实现 | v0.6.0b |
| 全局 `run_id -> workspace` 反查索引（`runs.json` 或等效） | ❌ 无 | v0.6.0c |
| 持久化 run 状态存储（非内存，支持跨进程查询） | ❌ 无 | v0.6.0c |
| run 取消机制（信号/标志文件/event，含 workspace 处置策略） | ❌ 未定义 | v0.6.0c |

最小 HTTP API（参考，不在本文档实现范围内）：

```text
POST /runs
GET  /runs/{run_id}/status
GET  /runs/{run_id}/results
POST /runs/{run_id}/cancel
```

---

## 6. 依赖关系与执行顺序

```text
v0.5.3（profile 对比）
    ↓
v0.5.4（context 修复 + custom_v8_005 调查）
    ↓
v0.6.0a（runtime 正确性基础）
    ↓
v0.6.0b（SWE-bench 引入）
    ↓
v0.6.0c（可选：job manager + HTTP API）
```

说明：

- `v0.6.0a` 是 `v0.6.0b` 的前置条件，因为 SWE-bench 需要更严格的 workspace 语义
- `v0.6.0b` 不必等待 API 层；它应尽早进入，用来提高配置评估分辨率
- `v0.6.0c` 继续后置，不得阻塞 `v0.6.0a / v0.6.0b`

---

## 7. 不在本版本范围内

以下内容明确排除在 `0.6.0` 之外：

- 跨进程的完整 resume（恢复 workspace 文件、对话历史、loop 状态）
- `compare_configs()` 内的并行任务执行
- Workspace 自动清理命令（`coder-agent eval clean`）
- 任何 HTTP API 层实现（属于 `v0.6.0c`）
- `chat` / 交互式 REPL 的 workspace 管理重构
- 全量 SWE-bench 数据集的公共榜单追分
- 未经版本固定的临时任务抽样比较
