# Improvement Report v11 - Bug Fixes, Robustness, Code Quality, Tests

> Date: 2026-04-01
> Scope: small improvements — bug fixes, robustness hardening, code quality, test coverage expansion

---

## Overview

v11 is a cleanup and hardening round.

It does not start a new benchmark-promotion cycle, and it does not introduce new agent capabilities.

This round focused on technical debt accumulated after the `0.4.0` baseline closure: real runtime
bugs identified by code review that were not previously hit in benchmarks, robustness gaps around
API resilience and config validation, and test coverage holes for core modules that had zero test
coverage.

The goal of v11 was:

- fix the four confirmed runtime bugs first
- add robustness guards for known operational risks
- expand test coverage to 100+ tests across all critical modules
- confirm no regression on the existing test suite

---

## Stage 1: Bug Fixes

### 1.1 `ProcessLookupError` in Shell Tool

**Problem**

`_terminate_process_tree()` in `coder_agent/tools/shell_tool.py` called
`os.killpg(proc.pid, signal.SIGKILL)` without a guard. If the process group exited between the
`returncode is not None` check and the kill call, this raised `ProcessLookupError` (a subclass
of `OSError`), which propagated as an unhandled exception to the timeout path in
`RunCommandTool.execute()`.

**Implementation**

Wrapped the `os.killpg` call in `try/except OSError: pass`. The process is already gone in this
case, so swallowing the exception is correct.

**Result**

The termination path now handles the race condition silently. No behavioral change for the common
case.

---

### 1.2 `message_tokens` Misalignment in `MessageHistory`

**Problem**

`coder_agent/core/context.py` maintained two parallel lists: `messages` and `message_tokens`. The
intent was that they stay in sync so that `truncate()` could pop from both simultaneously to
account for token usage.

However, `message_tokens` was only appended for `assistant` messages with usage data. User, tool,
and assistant-without-usage messages were never added to `message_tokens`. This meant the two
lists had different lengths. When `truncate()` popped `messages[0]` (usually a user message) and
also popped `message_tokens[0]`, it was actually removing the token count of the first
*assistant* message, not the user message — misattributing token costs during context truncation.

A secondary gap: the truncation notice inserted at the end of `truncate()` was added to
`messages` but not `message_tokens`, breaking alignment again after every truncation cycle.

**Implementation**

Three coordinated changes:

1. `add_message()` — appended `(0, 0)` to `message_tokens` for all non-assistant messages, making
   the two lists strictly parallel from the start.

2. `truncate()` pop path — removed the `if self.message_tokens:` guard, which was only present to
   mask the misalignment. With parallel lists, the pop is always correct.

3. `truncate()` notice insertion — added a matching `self.message_tokens.insert(0, (0, 0))` when
   inserting the truncation notice, preserving alignment after every truncation event.

**Result**

Token accounting during context truncation is now correct. The truncation notice no longer
silently breaks the parallel structure.

---

### 1.3 Malformed Tool Call `KeyError` in `execute.py`

**Problem**

`_execute_single()` in `coder_agent/tools/execute.py` accessed `call["id"]`, `call["name"]`, and
`call["input"]` before the `try` block. If the LLM produced a tool call with a missing field,
this raised `KeyError` outside the exception handler, propagating as an unhandled exception to
`execute_tools()` and crashing the entire batch rather than returning a clean error response.

**Implementation**

Wrapped the dict unpacking in its own `try/except (KeyError, AttributeError)` block that returns
a well-formed error response with `error_kind="tool_error"`. This feeds naturally into the
existing self-correction path rather than halting the loop.

**Result**

Malformed tool calls from the LLM now produce recoverable `ToolError` responses instead of
crashing the batch.

---

### 1.4 Silent Observation Truncation in Trajectory Recording

**Problem**

`record_trajectory_step()` in `coder_agent/core/agent_run_context.py` stored
`observation[:500]` without any marker. When a command produced several kilobytes of output,
the trajectory record appeared to be short, hiding the actual error context from downstream
analysis.

**Implementation**

Extracted the limit as a named constant `_OBSERVATION_MAX_CHARS = 500` and added a visible
` ...[truncated]` suffix when truncation occurs.

**Result**

Truncated observations are now clearly marked in trajectory files. The constant also makes the
limit easy to find and adjust.

---

## Stage 2: Robustness

### 2.1 LLM API Retry with Exponential Backoff

**Problem**

`LLMClient.chat()` had no retry or timeout logic. A single transient `APIConnectionError` or
`APITimeoutError` immediately failed the agent run. This was the root cause of the API
instability in long compare runs flagged in `IMPROVEMENT_SUMMARY_0_4_0`.

**Implementation**

Added a retry loop around `client.chat.completions.create()`:

- 3 retries (4 total attempts)
- Delays before retries 2/3/4: 1s, 2s, 4s + up to 0.5s jitter
- Only retries `openai.APIConnectionError` and `openai.APITimeoutError`; other exceptions
  propagate immediately

**Result**

Transient network errors in long benchmark runs are now recovered automatically. The maximum
added latency for a fully-retried call is about 7.5s before re-raising.

---

### 2.2 Tightened `classify_error()` Catch-All

**Problem**

The final fallback branch in `classify_error()` triggered on `"error"` as a substring, which
matched benign strings like `"no error"` or `"0 errors"`. While `classify_error` is only called
on text already gated by a non-zero exit code, false positives still produced misleading
self-correction hints.

**Implementation**

Replaced the bare `"error"` match with a multi-signal check: the fallback now fires only when
an explicit `"traceback"` is present, or when at least two of the three signals (`"traceback"`,
`"error:"`, `" failed"`) appear together.

**Result**

Benign output containing the word "error" no longer triggers `LogicError` classification.
True error output still matches because it typically contains `"traceback"` or multiple signals.

---

### 2.3 SQLite Connection Timeout

**Problem**

`MemoryManager.__init__()` called `sqlite3.connect()` without a timeout. A locked database from
a concurrent eval worker would block the calling thread indefinitely.

**Implementation**

Added `timeout=10` to `sqlite3.connect()`.

**Result**

The connection attempt now raises `sqlite3.OperationalError` after 10 seconds rather than
blocking forever.

---

### 2.4 Config Validation at Startup

**Problem**

Invalid config values such as `CODER_MAX_STEPS=0` or `CODER_MAX_RETRIES=-1` were not caught at
load time. They produced confusing downstream runtime errors deep inside the agent loop.

**Implementation**

Added a `validate_config(config)` function that checks `max_steps >= 1`, `max_retries >= 0`, and
`terminal_timeout >= 1`. It is called immediately after `cfg = Config()` at module level, so
bad values fail at import time with a clear `ValueError` message.

**Result**

Configuration errors now surface at startup with an explicit message rather than as cryptic
failures during a benchmark run.

---

### 2.5 Trajectory Analysis In-Memory Caching

**Problem**

`TrajectoryAnalyzer._load()` re-read and re-processed the full JSONL file on every call.
`compare_experiments()` calls `compute_statistics` → `_load` once per experiment per metric,
meaning the same file was parsed multiple times in a single report.

**Implementation**

Added `self._cache: dict[str, list[dict]] = {}` to `TrajectoryAnalyzer.__init__()`. `_load()`
now returns the cached result on the second and subsequent calls for the same experiment ID.

**Result**

`compare_experiments` and multi-method analysis calls now hit disk only once per experiment ID
per `TrajectoryAnalyzer` instance.

---

## Stage 3: Validation

### Test Suite

Command:

```bash
uv run pytest
```

Result:

- **108/108 tests passed**

Previous baseline: `60/60`.

New test files and extensions:

| File | New Tests |
|------|-----------|
| `tests/test_context_compression.py` | 13 new — `compress_observation` edge cases, `message_tokens` alignment regression |
| `tests/test_file_tools.py` | +14 new — `WriteFileTool` (write/edit/traversal), `ListDirTool`, `ReadFileTool` edge cases |
| `tests/test_search_tool.py` | 10 new — ripgrep + Python fallback, file_glob, case sensitivity, max_results, traversal |
| `tests/test_shell_tool.py` | +5 new — nonzero exit code, stdout/stderr capture, blocked commands |
| `tests/test_config.py` | 7 new — `validate_config()` valid and invalid values |

### Local Gate

Command:

```bash
uv run python -m coder_agent --help
```

Result: CLI help path passed.

---

## Interpretation

v11 closes four confirmed runtime bugs and addresses five robustness gaps identified through code
review after `0.4.0`.

What this report demonstrates:

1. the shell tool termination path now handles the process-already-gone race without crashing
2. context truncation token accounting is now correct and the parallel-list invariant is enforced
3. malformed LLM tool calls produce recoverable errors instead of batch crashes
4. transient API connection errors are retried with backoff before failing a run
5. invalid configs fail loudly at startup rather than silently during benchmarks

What v11 does **not** demonstrate:

- no new benchmark promotion cycle
- no new public baseline artifact naming
- no capability changes that affect pass rates on HumanEval or Custom

The practical outcome is:

> the `0.4.0` codebase is now materially harder to break at runtime, with 108 passing tests
> covering modules that previously had zero test coverage

---

## Summary

v11 should be understood as a correctness and coverage report, not a new baseline report.

It accomplished:

- four runtime bug fixes (process kill race, token misalignment, KeyError crash, silent truncation)
- five robustness additions (API retry, error classifier tightening, SQLite timeout, config
  validation, analysis caching)
- test suite expanded from 60 to 108 tests, with five new or extended test modules covering
  `WriteFileTool`, `ListDirTool`, `SearchCodeTool`, `MessageHistory`, and config validation

The `0.4.0` benchmark baselines remain the current source of truth. v11 makes the runtime
underneath them more reliable.
