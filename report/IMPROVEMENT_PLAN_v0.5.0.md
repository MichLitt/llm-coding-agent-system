# Improvement Plan v0.5.0 — Context Management & Memory

**Date:** 2026-04-03 (rev 4 — post three Codex review rounds)
**Branch:** claude/frosty-raman
**Baseline:** v0.4.5 — C6=85.0% (34/40), C4=70.0% (28/40), C3=42.5% (17/40)

---

## 0. Background & Motivation

v0.4.5 locked in the first accurate ablation on the 40-task Custom suite. The 6 remaining
C6 failures and the observed feature-contribution rankings motivate targeted improvements
to context management and memory.

### 0.1 Failure Analysis

| Task | Termination | Notes |
|------|------------|-------|
| custom_medium_005 | loop_exception | Cause TBD — inspect trajectory before assuming doom-loop |
| custom_v8_005 | loop_exception | Cause TBD — inspect trajectory before assuming doom-loop |
| custom_medium_011 | retry_exhausted | Logic error; correction alone cannot break cycle |
| custom_hard_004 | max_steps | Task complexity exceeds 15-step budget |
| custom_hard_005 | retry_exhausted | Multi-file refactor; agent loses track of progress |
| custom_hard_010 | max_steps | Task complexity exceeds 15-step budget |

> **Note on loop_exception:** `TERMINATION_LOOP_EXCEPTION` maps any unhandled exception
> to this reason (broad `except Exception` in `agent_loop.py:302`). It does NOT
> specifically mean repeated identical tool calls. Inspect saved JSONL trajectories
> for these two tasks before attributing cause.

### 0.2 Pre-observations: What Is Actually Broken Today

Three systemic issues were found during plan preparation, each more significant than
the individual task failures above:

**Issue A — Context management is dormant**

`MessageHistory.truncate()` triggers when `total_tokens > context_window_tokens`
(180,000). Token counting only increments on assistant messages; user/tool messages
get (0, 0). In practice `total_tokens` almost never reaches 180,000. Therefore:
- `truncate()` almost never fires
- `compress_observation()` almost never fires (it is only called inside `truncate()`)
- The existing `summary_threshold = 6000` in config.yaml has no call site

Additionally, `truncate()` unconditionally inserts `[Earlier history has been truncated.]`
into every run from step 2 onwards — even when no truncation occurred. This is a
pre-existing bug to be noted.

**Issue B — Memory writes are disabled in eval mode**

`runner.py:137` passes `finalize_trajectory=False` to every eval task.
`agent_run_context.py:97` gates `record_task()` behind `if finalize_trajectory`.
**task_history writes never happen during benchmarks.**

Separately, the existing workspace IS reused across all tasks in a run
(`runner.py:127`: `workspace = cfg.agent.workspace`), so `project_id` is
already stable. The problem is exclusively the write gate, not project_id churn.

**Issue C — C4's +27.5pp gain over C3 is unexplained under eval mode**

If memory writes never happen and `if recent:` prevents injection when history is
empty, C4 and C3 should behave identically in eval. Yet C4=70% >> C3=42.5%.

Hypotheses (in order of likelihood):
1. The persistent memory DB (`memory/agent_memory.db`) contained records from
   prior interactive runs, injected into C4's eval tasks via `get_recent_tasks()`
2. Statistical variance on 40 binary outcomes (σ ≈ 7.9pp at 50% base rate)
3. Some other undiscovered code path

**This must be investigated before any investment in M2/M3 retrieval work.** If
Hypothesis 1 is correct, the v0.4.5 C4 result is a confounded measurement and C4's
true baseline on a clean DB is unknown.

### 0.3 Ablation Plumbing Gap

`cfg.context` is a module-level singleton shared across all agents. The ablation
runner varies `agent_config` per-run via `make_agent()`, but `make_agent()` has no
`context_config` parameter. Context-feature ablations (C_ctx1/2/3) require either:
- A `context_config` parameter threaded through `make_agent()` → `Agent`
- Or runtime mutation of `cfg.context` fields between runs

This plumbing must be built before the ablation matrix is valid.

---

## 1. Scope

v0.5.0 is split into two sequential parts:

- **Part A — Context Management** (`v0.5.0a`): Three independently ablatable context
  features, plus required infrastructure changes
- **Part B — Memory** (`v0.5.0b`): Fix the eval write gate; investigate C4 mystery;
  enrich failure records; improve retrieval

Both parts share a prerequisite: **ablation plumbing** (§1.1).

### 1.1 Prerequisite: Ablation Config Threading (Day 0, Part A)

Add `context_config: dict | None = None` parameter to `make_agent()` in `factory.py`.
When provided, override the relevant `cfg.context` fields for that agent's run only.
Implement as a shallow copy of `cfg.context` fields stored on the `Agent` instance,
read by `MessageHistory` via the agent rather than from the global `cfg`.

**Config model unification:** All new feature flags introduced in v0.5.0 are passed
as keys inside a single `experiment_config: dict` argument (consistent with the
existing `agent_config` pattern). This avoids per-feature parameter explosion:
```python
experiment_config = {
    "doom_loop_threshold": 3,           # 0 = disabled
    "observation_compression_mode": "smart",  # "smart" | "rule_based"
    "history_compaction_mode": "semantic",    # "semantic" | "rule_based"
    "history_compaction_message_threshold": 20,
    "keep_recent_turns": 6,
    "enable_approach_memory": True,
    "memory_lookup_mode": "similarity",  # "recency" | "similarity"
}
```

**Ablation CLI extension:** `run_ablation.py` currently maps named presets to hard-coded
dicts. Rather than adding individual `C6_ctx1`, `C6_ctx2`, etc. entries to `CONFIG_PRESETS`,
add a `--experiment-config JSON` flag that accepts a JSON blob of overrides applied on
top of the base preset. This keeps the preset registry small while supporting arbitrary
ablation configs.

**DB isolation:** Per-config ablation requires isolated memory DBs to prevent cross-config
contamination. When `config_label` is provided to `make_agent()`, resolve:
```python
db_path = cfg.agent.memory_db_path.parent / f"agent_memory_{config_label}.db"
```
Default (no label) uses `agent_memory.db` unchanged.

**Two-layer config model:** The `agent.X` / `context.X` annotations in subsequent
sections denote the **config.yaml field paths** for default values (readable in
interactive mode and as ablation defaults). At runtime, `make_agent()` applies
the corresponding flat key from `experiment_config` as a per-run override — e.g.,
`experiment_config["doom_loop_threshold"]` overrides `cfg.agent.doom_loop_threshold`.
Implementers should add the new field to both `config.py` (dataclass) and `config.yaml`
(default value), then read it via `experiment_config.get("key", cfg.agent.key)` in the
agent loop so interactive mode and ablation mode both work.

This is the prerequisite for all context ablations and for Part B's clean DB baseline.

---

## 2. Part A: Context Management

### 2.1 C_ctx1 — Doom-Loop Detection

**Prerequisite:** Inspect JSONL trajectories for `custom_medium_005` and `custom_v8_005`
on Day 0. If exception traces show repeated identical tool calls, proceed as designed.
If not, adjust the target failure mode.

**Problem:**
Even if the two specific `loop_exception` failures are not doom-loops, a consecutive
identical-failure detector addresses a real pattern visible in C3/C4 `retry_exhausted`
failures: the agent re-issues the same failing command without awareness that it has
already failed in the same way.

The existing `last_error_signature` in `LoopState` tracks the most recent error, but
resets on each new error type. There is no count of consecutive repetitions.

**Design:**

Add to `LoopState` in `agent_loop.py`:
```python
consecutive_identical_failures: int = 0
last_failing_call_sig: str | None = None
```

On each tool batch that produces an error (`batch.saw_nonzero_exit or
batch.saw_recoverable_tool_error`), compute a batch-level signature that includes
both tool names and a brief content digest of the key argument (to distinguish
"same tool, different args" from a genuine repeat):
```python
def _tool_call_sig(tool_use: dict) -> str:
    name = tool_use["name"]
    # Take first 80 chars of the most distinctive argument (cmd for shell, path for file)
    args = tool_use.get("input", {})
    key_arg = args.get("cmd") or args.get("path") or args.get("content", "")
    return f"{name}:{str(key_arg)[:80]}"

sig = f"{sorted(_tool_call_sig(t) for t in turn.tool_uses)}:{batch.detected_error}"
```

Update the counter:
- If `sig == state.last_failing_call_sig`: `consecutive_identical_failures += 1`
- Else: `consecutive_identical_failures = 1`; `state.last_failing_call_sig = sig`
- On any **successful** (non-error) batch: reset both to `0` / `None`

If `consecutive_identical_failures >= doom_loop_threshold` (from context_config,
default 3), inject **once per occurrence** (add a single `"user"` message at the
top of the next `history.messages` via `history.add_message("user", warning_text)`)
before the LLM call. Do not re-inject on the immediately following step unless a new
doom threshold is crossed again after a success reset:
```
[System] You have issued the same failing command {n} times in a row without
progress. This approach is not working. Stop and try a fundamentally different
strategy.
```

**New config field:** `agent.doom_loop_threshold: int = 3` (0 = disabled, per agent_config)
**Files changed:** `agent_loop.py` (LoopState + loop body)

---

### 2.2 C_ctx2 — Eager Observation Compression

**Problem:**
`compress_observation()` is only called inside `truncate()`, which almost never fires.
Replacing the compressor function alone has zero effect during normal runs.

The fix is to move compression to **observation ingestion time** (`add_message`), so
every tool result is compressed before entering `MessageHistory`, independent of
whether the token threshold is ever reached.

**Design:**

Change `MessageHistory.add_message()`: when `role == "tool"`, call
`compress_observation(str(content))` on the content before storing. This makes
compression always-active, not threshold-gated.

Additionally, replace the two existing mechanical compressors with content-aware ones,
controlled by a new config field `context.observation_compression_mode`:

**pytest/terminal output** (`_compress_terminal_smart`):
1. Extract all `FAILED` and `ERROR` blocks (up to 3), keeping assertion + 10 lines
2. Always preserve the final summary line (`N passed, M failed in Xs`)
3. Emit `[N passing tests omitted]` for discarded passing-test lines
4. Fallback: if no FAILED/ERROR found, keep existing tail-30 logic

**File content** (`_compress_file_smart`):
1. Preserve import block
2. Preserve all `def`/`class` signature lines with first docstring line
3. Omit function bodies; emit `[body: N lines]` placeholder per omission
4. If result is longer than original: return original unchanged
5. **Python detection is content-based**, not extension-based: check whether the
   observation string contains at least one `def ` or `class ` line. This works
   because `compress_observation()` receives a plain string with no path metadata.
   Non-Python tool outputs (JSON, shell stdout, etc.) do not contain these markers
   and fall through to existing logic.

The "recently referenced function body" concept from the original plan is **dropped**:
it requires recency metadata not available in `compress_observation()`'s string-only
interface.

**New config field:** `context.observation_compression_mode: "smart" | "rule_based"`
(independent of history compaction mode)
**Files changed:** `context.py` (add_message eager compression + new compressor variants)

---

### 2.3 C_ctx3 — Semantic History Compaction

**Problem:**
`summary_threshold = 6000` (config.yaml) has no call site and is unused.
The real truncation threshold (180,000) is never reached.

For hard multi-step tasks, the agent accumulates growing context without compression,
eventually re-attempting completed sub-tasks when the context is silently truncated.
The two `max_steps` failures on hard tasks likely involve this pattern.

**Design:**

Use **message count** as the compaction trigger (not `total_tokens`, which only
increments on assistant messages with usage — almost never populated via the current
`llm_client.chat()` call paths). At the top of each loop step, after `history.truncate()`:

```python
# in agent_loop.py, top of the step loop
msg_threshold = experiment_config.get("history_compaction_message_threshold", 20)
if (
    experiment_config.get("history_compaction_mode") == "semantic"
    and len(agent.history.messages) > msg_threshold
):
    state.exception_stage = "history.compact"
    await agent.history.compact(agent.client, agent._params(), keep_recent=KEEP_RECENT_TURNS)
    state.exception_stage = None
```

`compact()` is implemented on `MessageHistory` but receives `client` and `params`
as arguments (not stored on `MessageHistory`) to avoid the interface gap. The params
dict must include `model` (required by most LLM backends), alongside optional
`max_tokens` and `temperature`. The response is an `LLMResponse` object (the return
type of `client.chat()`); extract the text content from its `.content[0].text` path,
not bare `.content`:

```python
async def compact(self, client, params: dict, keep_recent: int = 6) -> None:
    if len(self.messages) <= keep_recent:
        return
    to_compress = self.messages[:-keep_recent]
    to_keep = self.messages[-keep_recent:]
    tokens_to_compress = self.message_tokens[:-keep_recent]
    tokens_to_keep = self.message_tokens[-keep_recent:]

    summary_response = await client.chat(
        messages=to_compress,
        system=(
            "Summarize the agent's work so far as a structured JSON object with keys: "
            "task_goal, completed_steps (list), files_modified (list), current_state, "
            "failed_approaches (list), open_issues (list). "
            "Be concise. Each list item <= 1 sentence."
        ),
        tools=[],
        **{k: v for k, v in params.items() if k in ("model", "max_tokens", "temperature")},
    )
    # LLMResponse.content is a list of content blocks; extract text from first block
    if hasattr(summary_response, "content") and summary_response.content:
        block = summary_response.content[0]
        summary_text = block.text if hasattr(block, "text") else str(block)
    else:
        summary_text = str(summary_response)
    summary_msg = {
        "role": "user",
        "content": f"[Context compacted — {len(to_compress)} messages summarized]\n{summary_text}",
    }

    # Bookkeeping: rebuild parallel structures
    compressed_tokens = sum(inp + out for inp, out in tokens_to_compress)
    summary_est_tokens = len(summary_text) // 4

    self.messages = [summary_msg] + list(to_keep)
    self.message_tokens = [(0, summary_est_tokens)] + list(tokens_to_keep)
    self.total_tokens = (self.total_tokens - compressed_tokens) + summary_est_tokens
```

**Failure safety:** If `compact()` raises any exception, the outer `except Exception`
in `agent_loop.py` catches it with `state.exception_stage = "history.compact"`,
distinguishing it from agent logic failures. The run then terminates gracefully
rather than silently continuing with a broken history state. A future improvement
could fall back to `truncate()`, but for the ablation phase, clean failure is
preferable to silent degradation.

**New config field:** `context.history_compaction_mode: "semantic" | "rule_based"`
(independent of observation compression mode)
**New config field:** `context.history_compaction_message_threshold: int = 20`
(replaces the unused `summary_threshold`; message count is the reliable trigger)
**New config field:** `context.keep_recent_turns: int = 6`
**Files changed:** `context.py` (MessageHistory.compact), `agent_loop.py` (trigger),
`config.py` (new ContextConfig fields)

---

### 2.4 Fix: Spurious Truncation Notice (Pre-existing Bug)

`truncate()` unconditionally inserts `[Earlier history has been truncated.]` even
when no truncation occurred, injecting a misleading signal into every run from step 2.

**Fix:** Move the notice insertion inside the `while` body (only when a message is
actually popped) and add a boolean `did_truncate` flag:

```python
def truncate(self) -> None:
    did_truncate = False
    while self.total_tokens > self.context_window_tokens and self.messages:
        ...  # existing compression / pop logic
        did_truncate = True
    if did_truncate and self.messages:
        notice = {"role": "user", "content": "[Earlier history has been truncated.]"}
        if self.messages[0] != notice:
            self.messages.insert(0, notice)
            self.message_tokens.insert(0, (0, 0))
```

**Files changed:** `context.py`

---

### 2.5 Ablation Strategy for Part A

**Step 0 (Day 0):**
1. Inspect trajectories for `custom_medium_005` / `custom_v8_005`
2. Add ablation config threading to `make_agent()` (§1.1)
3. Apply truncation notice bugfix (§2.4) — affects all runs, apply before any ablation

Run five configs against the 40-task baseline:

| Config | doom_loop | obs_compression | hist_compaction | Expected gain target |
|--------|-----------|-----------------|-----------------|----------------------|
| C6 | off | rule_based | rule_based | baseline: 85% |
| C6_ctx1 | on (thresh=3) | rule_based | rule_based | loop_exception → recovery |
| C6_ctx2 | off | smart (eager) | rule_based | retry_exhausted on logic errors |
| C6_ctx3 | off | rule_based | semantic | max_steps on hard tasks |
| C6_ctx_all | on | smart (eager) | semantic | combined |

**Required ablation metrics (feature activation verification):**
In addition to BenchPass/AvgSteps/termination counts, each run must emit:
- `doom_loop_warnings_injected`: count of doom-loop warnings fired
- `observations_compressed`: count of observations compressed eagerly
- `compaction_events`: count of history compactions performed
A null result without these counters is uninterpretable.

**Activation counter pipeline:** Counters flow through the existing data structures:

1. **`LoopState`** (agent_loop.py): add integer counters, incremented in-loop
2. **`TurnResult`** (agent_types.py): add an `extra: dict = field(default_factory=dict)`
   field (avoids adding individual fields per experiment); counters are written as
   `result.extra["doom_loop_warnings_injected"] = state.doom_loop_warnings_injected`
   etc. in `build_turn_result()` / `_build_final_result()`.
3. **`EvalResult`** (metrics.py): add `activation_counters: dict = field(default_factory=dict)`;
   populated from `turn_result.extra` in the runner after each task.
4. **JSON output**: `EvalResult` already serializes to JSON in the runner; the
   `activation_counters` dict appears as a nested object in each task's result entry.
   The analysis CLI can aggregate these per config.

**Latency constraint:** C6_ctx3 wall-clock time ≤ +20% vs C6 baseline.

---

## 3. Part B: Memory

### 3.1 Investigation First: The C4 Mystery (Day 0, Part B)

Before any M1/M2/M3 implementation, run two diagnostic checks:

**Check 1 — DB state at ablation time:**
```bash
sqlite3 memory/agent_memory.db "SELECT COUNT(*) FROM task_history;"
```
If non-zero, the v0.4.5 C4 ablation was run against a pre-populated DB.
Record what records existed and from what dates.

**Check 2 — Controlled re-run:**
Run a 5-task C4 smoke test with `memory/agent_memory.db` deleted first.
If C4 score drops significantly vs C3 on those 5 tasks, Hypothesis 1 is confirmed
(pre-existing DB data was a confound). Record the clean-DB result as the true C4 baseline.

**Implication if Hypothesis 1 is confirmed:**
- The v0.4.5 C4=70% is a confounded measurement
- The "true" C4 baseline (clean DB) must be established before claiming M improvements
- M2/M3 retrieval improvements become much more meaningful: there IS value to unlock,
  it just requires enabling writes first

### 3.2 Fix: Enable Memory Writes in Eval Mode (prerequisite for M2/M3)

**Root cause:** `runner.py:137` passes `finalize_trajectory=False`, which gates
`record_task()` in `agent_run_context.py:97`.

**Fix:** Decouple memory writes from the `finalize_trajectory` flag. The current
`record_task()` call in `agent_run_context.py:finalize_turn()` is called from
**multiple code paths** (at minimum: `run_agent_loop()` main path,
`handle_completion_turn()` in `agent_turns.py:160`, and `handle_verification_auto_complete()`
in `agent_tool_batch.py:166`). Patching `finalize_turn()` alone may not cover all
paths if they bypass it.

**Correct fix: move `record_task()` to `Agent.run()`** (the single top-level entry
point), called once after `run_agent_loop()` returns, regardless of termination path.
Add a `record_memory: bool = True` kwarg to `Agent.run()` to allow opt-out:

```python
# agent.py  (Agent.run)
async def run(self, user_input: str, ..., record_memory: bool = True) -> TurnResult:
    result = await run_agent_loop(self, user_input, ...)
    if record_memory and self.memory and self._project_id:
        self.memory.record_task(self._project_id, user_input, result)
    return result
```

```python
# runner.py  — no change needed; record_memory defaults to True
turn_result = await agent.run(
    task.description,
    task_id=task.task_id,
    finalize_trajectory=False,  # unchanged
    ...
)
```

Remove the `record_task()` call from `finalize_turn()` / `agent_run_context.py` once
the Agent.run() call is in place, to avoid double-writes.

**Files changed:** `agent.py`, `agent_run_context.py` (remove old call site), `runner.py`

---

### 3.3 M1 — Within-Run Tried-Approaches Tracking

**Problem:** When correction triggers repeated retries, the agent lacks a persistent
list of already-tried-and-failed approaches within the current task. `last_error_signature`
tracks only the most recent error.

**Design:**

Add to `LoopState`:
```python
tried_approaches: list[dict] = field(default_factory=list)
```

On each error batch where `state.retry_count >= 1`, append at **turn/batch level**
(a single model turn may call multiple tools — record the batch, not per-tool):
```python
{
    "attempt": state.retry_count,
    "tools": [t["name"] for t in turn.tool_uses],
    "error": batch.detected_error,
    "observation_head": combined_observation[:200],
}
```

Before each LLM call, if `len(tried_approaches) >= 2`, generate a fresh injection
message and **replace** any prior injection in the history (scan for a message whose
content starts with the sentinel `"[Memory/Approaches]"`, remove it, then prepend the
updated one). This prevents accumulation of stale entries across retry steps:
```
[Memory/Approaches] Approaches already tried and failed in this task:
  1. {tools} → {error}: {observation_head}
  2. ...
Do not repeat these. Use a different approach.
```

**Config flag:** `agent.enable_approach_memory: bool = True`
(see §1.1 two-layer config model; runtime override: `experiment_config["enable_approach_memory"]`)
**Files changed:** `agent_loop.py` (LoopState, injection), `config.py`

---

### 3.4 M2 — Richer Failure Records

**Problem:** `task_history` stores `success`, `steps`, `tool_calls` (name list only).
No failure reason, no error classification.

**TurnResult gap:** `TurnResult` has `termination_reason: str` and
`error_details: list[str]` (free-form strings). There is no structured `error_types`
field. M2 will derive error types from `error_details` parsing rather than adding
a new TurnResult field, to minimise interface changes.

**Design:**

Schema migration in `manager.py` `init_db()`:
```python
def _migrate_task_history(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(task_history)")}
    for col, typedef in [
        ("termination_reason", "TEXT"),
        ("error_summary",      "TEXT"),   # derived from error_details[:2], joined
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE task_history ADD COLUMN {col} {typedef}")
    conn.commit()
```

Update `record_task()` to accept and store `termination_reason` and an `error_summary`
(first 300 chars of `"\n".join(result.error_details)`).

Update `get_recent_tasks()` to return these fields.

**Files changed:** `manager.py`

---

### 3.5 M3 — Cross-Task Retrieval Within a Benchmark Run

**Corrected premise:** Since eval reuses `cfg.agent.workspace`, all tasks in one run
already share the same `project_id`. The retrieval problem is simply that no records
exist (writes disabled — fixed in §3.2). Once §3.2 is in place, within-run cross-task
retrieval works automatically via the existing `get_recent_tasks()`.

The remaining improvement is **relevance ranking**: instead of "most recent N tasks",
find tasks with similar descriptions (useful for sequential eval where similar task
types cluster).

Add `get_similar_tasks()` to `MemoryManager`:
```python
def get_similar_tasks(self, project_id: str, description: str, n: int = 3) -> list[dict]:
    keywords = _extract_keywords(description)  # lowercase, strip stopwords
    rows = self._conn.execute(
        "SELECT * FROM task_history WHERE project_id = ? ORDER BY created_at DESC LIMIT 100",
        (project_id,)
    ).fetchall()
    scored = [(len(keywords & _extract_keywords(r["description"])), dict(r))
              for r in rows if len(keywords & _extract_keywords(r["description"])) >= 2]
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:n]]
```

`_extract_keywords(text: str) -> set[str]`: lowercase, split on non-alphanumeric,
remove tokens in a minimal stopword set (`{"a","an","the","to","in","of","and","or",
"with","for","that","this","is","are","be","was","were","add","fix","write","use",
"implement","make","create","update","return","get","set","run","check"}`), keep
remaining tokens of length ≥ 4. Returns a `set[str]`. Does not stem.

Update `seed_run_context()` to use `get_similar_tasks()` when `memory_lookup_mode == "similarity"`,
falling back to `get_recent_tasks()` for `"recency"` (default).

**Injected prompt format** (specifying what the agent sees with M2+M3 fields):
```
[Memory] Similar completed tasks in this run:
  1. [OK] "fix async downloader" (5 steps) — termination: verification_passed
  2. [ERR] "add context manager to DBConn" (8 steps) — termination: retry_exhausted
     Summary: AssertionError: __exit__ not called on exception...
```
Cap total injection at 400 chars.

**New config field:** `agent.memory_lookup_mode: "recency" | "similarity"`
**Files changed:** `manager.py`, `agent_run_context.py`, `config.py`

---

### 3.6 Ablation Strategy for Part B

**Day 0 (Investigation):** Run C4 mystery diagnostics (§3.1) before writing any code.

**DB isolation:** Each config uses its own DB file to prevent cross-config
contamination when configs run sequentially (per §1.1 prerequisite):
```python
# make_agent() in factory.py
db_path = cfg.agent.memory_db_path.parent / f"agent_memory_{config_label}.db"
```
This means C4_m1, C4_m3, etc. each get a fresh DB at the start of their run.
No pre-existing records from interactive sessions or other config runs can contaminate.

| Config | write_gate_fix | M1 | M2 | M3 | Notes |
|--------|---------------|----|----|-----|-------|
| C4 clean | on | off | off | off | True C4 baseline (clean DB) |
| C4_m1 | on | on | off | off | within-run approach tracking |
| C4_m3 | on | off | on | on | richer records + similarity (M2 is prerequisite of M3, always bundled) |
| C4_all | on | on | on | on | full memory stack |
| C6_m1 | on | on | off | off | C6 baseline (no ctx features) + M1 approach memory |

Note: **C4_m2 standalone is removed**. M2 (richer records) only has value when M3
(retrieval) reads those records — running M2 alone would write richer records but
never inject them, making it uninterpretable as an ablation step. M2 is always
bundled as the prerequisite of M3 in C4_m3 and C4_all.

**Required ablation metrics:**
- `memory_injections`: count of non-empty memory prompt injections
- `approach_memory_injections`: count of within-run approach summaries injected
- `db_records_written`: count of task_history rows written during the run

These counters use the same pipeline as Part A (§2.5): `LoopState` → `TurnResult.extra`
→ `EvalResult.activation_counters` → JSON output. A null result without these counters
cannot be interpreted.

---

## 4. Implementation Order

```
=== PART A ===

Day 0 (setup):
  - Inspect trajectories for custom_medium_005 / custom_v8_005
  - Fix truncation notice bug (§2.4) — zero-risk, applies to all runs
  - Add ablation config threading to make_agent() (§1.1)

Week 1:
  Day 1-2:  C_ctx1 doom-loop detection (consecutive failure counter)
  Day 3-4:  C_ctx2 eager observation compression + smart compressors
  Day 5:    Run C6_ctx1 + C6_ctx2 ablations; verify feature activation counters

Week 2:
  Day 1-3:  C_ctx3 semantic compaction (compact() + trigger in agent_loop)
  Day 4:    Run C6_ctx3 + C6_ctx_all ablations
  Day 5:    Decide which Part A features to promote to default

=== PART B ===

Day 0 (investigation):
  - C4 mystery diagnostics: DB record count + clean-DB smoke test

Week 3:
  Day 1:    Fix eval write gate (§3.2) — prerequisite for all M ablations
  Day 2:    M1 within-run approach tracking
  Day 3-4:  M2 schema migration + M3 similarity lookup (always bundled)
  Day 5:    Run Part B ablation matrix (C4 clean, C4_m1, C4_m3, C4_all, C6_m1)

Week 4 (wrap-up):
  Day 1-2:  Write IMPROVEMENT_REPORT_v0.5.0.md; promote winning configs
  Day 3:    Update BASELINE_0_5_0.md with new promoted results
```

---

## 5. Acceptance Criteria

A feature is **promoted** if:

1. BenchPass improves ≥ +3pp vs same base config on 40-task Custom suite
2. No regression on previously passing tasks
3. `uv run pytest` passes
4. AvgSteps increase ≤ +2.0
5. For C_ctx3: wall-clock per task ≤ +20%
6. Feature activation counter > 0 for ≥ 50% of tasks in a 10-task smoke run
   (proves the feature fired, not just that BenchPass happened to improve)

---

## 6. Risk Register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| v0.4.5 C4 result was confounded by pre-existing DB data | Medium | §3.1 diagnostic on Day 0 before any code; clean-DB baseline mandatory |
| Doom-loop threshold fires on valid repeated reads (after successful steps) | Low | Consecutive-failure only; resets on successful batch |
| compact() call fails mid-run | Low | exception_stage="history.compact"; run terminates with clean error vs silent corruption |
| Eager compression (C_ctx2) slows ingestion for large file reads | Low | Compression is CPU-only, sub-ms for typical tool outputs |
| Smart pytest compressor misses FAILED blocks in non-standard output formats | Medium | Fallback to tail-30 if no FAILED/ERROR markers found |
| Smart file compressor applied to non-Python files | Low | Content-based detection (requires `def `/`class ` markers); non-Python tool outputs lack these and fall through to rule-based logic |
| M3 similarity returns irrelevant results (< 2 keyword overlap) | Medium | Hard minimum 2-keyword overlap; cap injection at 400 chars; ablation will surface this |
| Record_task() write in eval introduces measurable per-task latency | Low | SQLite write is ~1ms; negligible vs 15-30s task duration |
| C_ctx3 compaction adds an extra LLM call per compaction event | Medium | Latency budget capped at +20% (§5 criterion 5); token cost logged via activation_counters; if cost exceeds budget, raise message threshold or use a smaller/cheaper model for the compaction call |

---

## 7. Parallel Execution Guide

v0.5.0 can be implemented using **multiple Claude Code instances in parallel git worktrees**.
This section specifies the stream split, file ownership, and handoff rules needed to
prevent conflicts and uncontrolled scope creep.

### 7.1 Stream Split Overview

Implementation is divided into two waves. Wave 1 streams are fully independent and can
start simultaneously. Wave 2 streams each have one upstream dependency.

```
WAVE 1 (start in parallel immediately)
  Stream A — Infra          §1.1
  Stream B — context.py     §2.4 + C_ctx2 + C_ctx3.compact()
  Stream C — Memory stack   §3.2 + §3.4

WAVE 2 (each starts after one Wave 1 dependency merges)
  Stream D — Loop body      C_ctx1 + C_ctx3 trigger + M1 + counters pipeline
                            → depends on: Stream A merged
                            → depends on: Stream B merged (for C_ctx3 trigger)
  Stream E — M3 retrieval   §3.5
                            → depends on: Stream C merged (for M2 schema)
```

### 7.2 File Ownership Per Stream

Each stream has three categories:
- **WRITE**: the stream owns these files; only this stream may modify them
- **READ**: read freely for context; do not modify
- **BLOCKED**: owned by another stream; if a change is needed here, stop and report — do not modify

#### Stream A — Infra (`§1.1`)

| Category | Files |
|----------|-------|
| WRITE | `coder_agent/cli/factory.py` |
| WRITE | `coder_agent/config.py` |
| WRITE | `coder_agent/cli/run_ablation.py` |
| WRITE | `coder_agent/core/agent.py` *(add `experiment_config` storage to `__init__` and pass-through to `run()`)* |
| READ | `coder_agent/core/agent_loop.py`, `coder_agent/core/context.py` |
| READ | `coder_agent/core/agent_run_context.py`, `coder_agent/memory/manager.py` |
| BLOCKED | everything else |

**Deliverable:** `make_agent()` accepts `experiment_config: dict | None` and `config_label: str | None`;
per-config DB path resolved; `--experiment-config JSON` flag added to `run_ablation.py`;
new config.yaml fields added with defaults.

---

#### Stream B — context.py (`§2.4` + `C_ctx2` + `C_ctx3.compact()`)

| Category | Files |
|----------|-------|
| WRITE | `coder_agent/core/context.py` |
| WRITE | `coder_agent/config.py` *(add `observation_compression_mode`, `history_compaction_mode`, `history_compaction_message_threshold`, `keep_recent_turns` to `ContextConfig`)* |
| READ | `coder_agent/core/agent_loop.py`, `coder_agent/core/agent.py` |
| BLOCKED | `agent_loop.py` (Stream D owns this), all memory files |

**Deliverable:** Three changes in `context.py` — `truncate()` spurious-notice fix (§2.4);
`add_message()` eager compression with smart compressors (C_ctx2); new async `compact()`
method (C_ctx3). `compact()` is implemented but not yet called — Stream D adds the call site.

> **Note on config.py conflict:** Stream A also writes `config.py`. Stream B only adds
> fields to `ContextConfig` (a separate dataclass from `AgentConfig`). If both streams
> touch config.py concurrently, merge will require a manual review of the two additions —
> they do not touch the same lines. Resolve by letting Stream A's config.py merge first,
> then Stream B rebases before its PR.

---

#### Stream C — Memory stack (`§3.2` + `§3.4`)

| Category | Files |
|----------|-------|
| WRITE | `coder_agent/core/agent.py` *(add `record_memory: bool = True` param to `Agent.run()`; move `record_task()` call here from `agent_run_context.py`)* |
| WRITE | `coder_agent/core/agent_run_context.py` *(remove old `record_task()` call site)* |
| WRITE | `coder_agent/memory/manager.py` *(M2 schema migration, `record_task()` update, `get_recent_tasks()` update)* |
| READ | `coder_agent/eval/runner.py`, `coder_agent/core/agent_loop.py` |
| BLOCKED | `agent_loop.py`, `context.py`, `runner.py` *(read only; do not add activation counter logic — that belongs to Stream D)* |

**Deliverable:** `record_task()` fires for every eval task (§3.2); `task_history` schema
has `termination_reason` + `error_summary` columns with migration (§3.4).

> **Note on agent.py conflict:** Stream A also writes `agent.py` (`__init__` changes).
> Stream C writes `Agent.run()`. These are different methods. Merge strategy: Stream A
> merges first; Stream C rebases onto it before its PR.

---

#### Stream D — Loop body (`C_ctx1` + `C_ctx3 trigger` + `M1` + counters pipeline)

**Starts after:** Stream A merged AND Stream B merged.

| Category | Files |
|----------|-------|
| WRITE | `coder_agent/core/agent_loop.py` *(LoopState additions; doom-loop counter; C_ctx3 trigger calling `history.compact()`; M1 tried-approaches tracking and sentinel injection)* |
| WRITE | `coder_agent/core/agent_types.py` *(add `extra: dict = field(default_factory=dict)` to `TurnResult`)* |
| WRITE | `coder_agent/eval/metrics.py` *(add `activation_counters: dict = field(default_factory=dict)` to `EvalResult`)* |
| WRITE | `coder_agent/eval/runner.py` *(populate `EvalResult.activation_counters` from `turn_result.extra` after each task)* |
| WRITE | `coder_agent/config.py` *(add `doom_loop_threshold`, `enable_approach_memory` to `AgentConfig`)* |
| READ | `coder_agent/core/context.py` *(read `compact()` signature; do not modify)* |
| READ | `coder_agent/core/agent.py`, `coder_agent/cli/factory.py` |
| BLOCKED | `context.py`, `manager.py`, `agent_run_context.py` |

**Deliverable:** All three loop features active and gated by `experiment_config` flags;
activation counters flow from `LoopState` → `TurnResult.extra` → `EvalResult.activation_counters`
→ JSON output.

---

#### Stream E — M3 retrieval (`§3.5`)

**Starts after:** Stream C merged.

| Category | Files |
|----------|-------|
| WRITE | `coder_agent/memory/manager.py` *(add `get_similar_tasks()` and `_extract_keywords()`)* |
| WRITE | `coder_agent/core/agent_run_context.py` *(update `seed_run_context()` to support `memory_lookup_mode == "similarity"`)* |
| WRITE | `coder_agent/config.py` *(add `memory_lookup_mode` to `AgentConfig`)* |
| READ | `coder_agent/core/agent_loop.py`, `coder_agent/core/agent.py` |
| BLOCKED | `agent_loop.py`, `context.py`, `runner.py` |

**Deliverable:** `get_similar_tasks()` implemented with `_extract_keywords()`; injected
prompt format per §3.5; `seed_run_context()` dispatches on `memory_lookup_mode`.

---

### 7.3 Files Potentially Modified Beyond This List

Claude Code instances commonly touch files not in the original plan. The following
files are **expected to be modified** but are not assigned to a specific stream —
assign to the stream that first needs them:

| File | Likely reason | Assign to |
|------|--------------|-----------|
| `tests/test_context.py` (new or existing) | C_ctx2/C_ctx3/§2.4 tests | Stream B |
| `tests/test_agent_loop.py` (new or existing) | C_ctx1/M1 tests | Stream D |
| `tests/test_memory.py` (new or existing) | M2/M3 tests | Stream E |
| `tests/test_factory.py` | experiment_config threading tests | Stream A |
| `coder_agent/core/__init__.py` | new exports if any | whichever stream adds exports |

**Rule:** If a Claude Code instance finds it must modify a BLOCKED file to make its
feature work, it must **stop and report** the specific change needed rather than
making the modification. Do not work around the constraint by duplicating logic.

### 7.4 Per-Instance Prompt Template

Use this template when spawning each Claude Code instance:

```
You are implementing Stream [X] of the v0.5.0 improvement plan.
Plan reference: report/IMPROVEMENT_PLAN_v0.5.0.md §[sections]

YOUR WRITE FILES (you own these — modify freely):
  [list from §7.2]

READ-ONLY FILES (read for context, do not modify):
  [list from §7.2]

BLOCKED FILES (owned by another stream — do not modify under any circumstances):
  [list from §7.2]

If you discover that a blocked file needs a change to make your feature work:
  1. Stop before making the change
  2. Describe exactly what change is needed and why
  3. Ask the user to coordinate with the owning stream

Scope rule: implement only what is described in the referenced plan sections.
Do not add error handling, refactor unrelated code, or add features beyond the spec.
If tests break in files outside your WRITE list, report them — do not fix them.
```

### 7.5 Merge Order

```
1. Stream A  (no deps)          → merge first; establishes experiment_config plumbing
2. Stream B  (no deps)          → merge after A (config.py rebase required)
3. Stream C  (no deps)          → merge after A (agent.py rebase required)
4. Stream D  (needs A + B)      → merge after both A and B are in main
5. Stream E  (needs C)          → merge after C is in main

config.py is touched by A, B, D, E — each rebase onto the previous merge before PR.
agent.py is touched by A and C — C rebases onto A.
agent_run_context.py is touched by C and E — E rebases onto C.
manager.py is touched by C and E — E rebases onto C (C adds columns; E adds methods).
```

---

## 8. Out of Scope for v0.5.0

- **C7 preset** (checklist + verification_gate): quick win, orthogonal; do as v0.4.6 patch
- **Persistent run state / API layer**: Phase 1 of strategic roadmap; deferred to v0.6.0
- **SWE-bench integration**: deferred to v0.6.0
- **Token accounting fix** (assistant-only counting): pre-existing issue, affects all configs
  equally; deferred as separate cleanup task
- **Global max_steps increase**: re-evaluate after C_ctx3 results
- **"Recently referenced function body" compression**: requires recency metadata not
  available in the current observation pipeline interface
