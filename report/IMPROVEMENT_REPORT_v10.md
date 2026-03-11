# Improvement Report v10 - Tool Error Recovery and Client Lifecycle Cleanup

> Date: 2026-03-11
> Scope: release-blocking runtime fixes and targeted regression reruns for `0.4.0`

---

## Overview

v10 is a post-RC fix iteration.

It does not start a new benchmark-promotion cycle, and it does not introduce a new provider abstraction layer.

This round focused on three release-blocking problems discovered after the `0.4.0` re-baselining work:

1. file-tool errors were still terminating runs too aggressively
2. `read_file(min_lines=...)` caused a real protocol-compatibility crash on `custom_hard_003`
3. `AsyncClient.aclose()` could still raise `RuntimeError: Event loop is closed` after benchmark runs finished

The goal of v10 was therefore:

- fix runtime correctness first
- re-run targeted regression checks
- confirm the known blockers are gone before attempting any further capability tuning

---

## Stage 1: Recoverable Tool Errors

### Problem

After the first `0.4.0` RC reruns, the Custom benchmark still had failures that were not pure capability misses.

The clearest example was `custom_hard_003`, where the agent produced:

- `ReadFileTool.execute() got an unexpected keyword argument 'min_lines'`

That failure class should not remain a hard runtime stop for an already-registered tool.

There was also a broader control issue:

- file-tool errors such as bad edit anchors, missing files, or parameter mismatches were being treated too close to infrastructure failures
- this prematurely ended runs that should instead have gone through the normal correction loop

### Implementation

v10 changes the tool/runtime contract in two ways:

1. `read_file` now supports line-range reads with:
   - `start_line`
   - `max_lines`
   - compatibility alias `min_lines`

2. tool execution now distinguishes:
   - **unknown tool** -> still a hard `tool_exception`
   - **known tool returned an error** -> recoverable `ToolError`, fed back into self-correction

Additional behavior changes:

- `"Error: ..."` tool results are now normalized as tool errors
- `ToolError` now has explicit guidance in the correction layer
- auto-complete on verification no longer fires on batches that still contain recoverable tool errors

### Result

The `custom_hard_003` failure mode changed from a protocol crash into an ordinary benchmark miss.

Before the fix:

- `custom_hard_003` could end with `tool_exception`

After the fix:

- `custom_hard_003` no longer crashes on `min_lines`
- the same task now fails as a normal `retry_exhausted` or `max_steps` capability-style miss depending on preset

That is the intended direction:

- runtime contract violation removed
- remaining miss exposed as a model/task quality problem instead of infrastructure failure

---

## Stage 2: Async Client Lifecycle Cleanup

### Problem

The first `0.4.0` RC benchmark work exposed a lifecycle bug:

- runs completed and wrote artifacts correctly
- but cleanup could still emit `AsyncClient.aclose()` / `Event loop is closed`

This was caused by reusing an async OpenAI-compatible client across separate `asyncio.run(...)` event loops and then attempting to close it after the original loop was already gone.

### Implementation

v10 adds explicit lifecycle handling across the stack:

- `LLMClient` now has `aclose()` and `close()`
- `Agent` now has `close()`
- `AgentSession` now has `close()`
- CLI `run`, default REPL/chat, eval runner, and LLM-based analysis all close owned resources explicitly

The important internal behavior change is:

- `LLMClient` now manages the underlying async client per active event loop instead of blindly reusing one client across unrelated `asyncio.run(...)` lifetimes

This keeps acquisition and cleanup inside the correct loop boundary.

### Result

The event-loop shutdown error is no longer present in the targeted reruns.

This closes the most important post-run cleanup defect discovered in the first `0.4.0` RC cycle.

---

## Stage 3: Validation

### Test Suite

Command:

```bash
uv run pytest
```

Result:

- **50/50 tests passed**

New coverage added for:

- `read_file` line-range support and `min_lines` compatibility
- normalization of `"Error: ..."` tool outputs into recoverable tool errors
- recoverable tool-error behavior in the agent loop
- unknown-tool hard-failure behavior
- idempotent `LLMClient.close()` / `LLMClient.aclose()`
- agent/session cleanup paths
- eval runner cleanup on both success and interruption

### Local Gate

Commands:

```bash
uv run python -m coder_agent --help
Write-Output '/exit' | uv run python -m coder_agent
```

Result:

- CLI help path passed
- default REPL startup and clean exit passed

### Targeted Custom Re-Run

#### Standalone `C4`

Command:

```bash
uv run python -m coder_agent eval --benchmark custom --preset C4 --resume --config-label custom_fixcheck_c4
```

Result:

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|--------|----------------|------------------|----------------|-----------|------------|
| `custom_fixcheck_c4` | `85.7% (18/21)` | `85.7% (18/21)` | `85.7% (18/21)` | `7.1` | `410` |

Failed tasks:

- `custom_hard_003` (`retry_exhausted`)
- `custom_v8_005` (`retry_exhausted`)
- `custom_v8_007` (`retry_exhausted`)

Important runtime outcome:

- no `tool_exception` remained in this standalone `C4` rerun

#### `C3/C4/C6` comparison rerun

Command:

```bash
uv run python -m coder_agent eval --benchmark custom --compare C3,C4,C6 --resume --config-label custom_fixcheck_cmp
```

Result:

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|--------|----------------|------------------|----------------|-----------|------------|
| `C3` | `95.2% (20/21)` | `95.2% (20/21)` | `95.2% (20/21)` | `5.4` | `410` |
| `C4` | `95.2% (20/21)` | `95.2% (20/21)` | `95.2% (20/21)` | `5.1` | `410` |
| `C6` | `71.4% (15/21)` | `71.4% (15/21)` | `71.4% (15/21)` | `7.1` | `410` |

Failed tasks:

`custom_fixcheck_cmp_C3`

- `custom_v8_005` (`retry_exhausted`)

`custom_fixcheck_cmp_C4`

- `custom_v8_005` (`retry_exhausted`)

`custom_fixcheck_cmp_C6`

- `custom_hard_003` (`max_steps`)
- `custom_v8_002` (`retry_exhausted`)
- `custom_v8_005` (`retry_exhausted`)
- `custom_v8_007` (`retry_exhausted`)
- `custom_v8_008` (`max_steps`)
- `custom_v8_009` (`retry_exhausted`)

Interpretation:

- `C3` and `C4` both reached `95.2%` in the targeted comparison rerun
- `C6` remained materially weaker on the expanded Custom suite
- the important release-blocking fix was not a score increase by itself, but removal of runtime failure modes from the `C3/C4` path

### HumanEval Smoke Check

Command:

```bash
uv run python -m coder_agent eval --benchmark humaneval --preset C6 --limit 10 --resume --config-label humaneval_fixcheck_c6_smoke
```

Result:

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|--------|----------------|------------------|----------------|-----------|------------|
| `humaneval_fixcheck_c6_smoke` | `100.0% (10/10)` | `100.0% (10/10)` | `100.0% (10/10)` | `1.0` | `410` |

Interpretation:

- the lifecycle fix did not introduce an obvious HumanEval regression on the smoke path

---

## Interpretation

v10 closes the most important post-RC runtime issues.

What this report demonstrates:

1. the tool protocol is now more robust against line-range and edit-target mismatches
2. known file-tool errors are now recoverable instead of being conflated with hard infrastructure failure
3. async client cleanup no longer emits the old event-loop-closed failure after runs finish

What v10 does **not** demonstrate:

- no new full official `0.4.0` benchmark promotion cycle
- no new public baseline artifact naming
- no evidence that `C6` should be promoted on Custom tasks

The most important remaining benchmark issue after v10 is now clearer:

- `custom_v8_005` is the most consistent remaining shared failure across the strongest Custom paths

That makes `custom_v8_005` the best next target for capability work, while the main runtime release blockers have already been addressed.

---

## Summary

v10 should be understood as a fix-and-validate report, not a new baseline report.

It accomplished:

- recoverable handling for common file-tool failures
- compatibility for `read_file(min_lines=...)`
- explicit async client and session cleanup
- regression expansion from `39` tests to `50`
- targeted reruns showing the original runtime blockers are gone

The practical outcome is:

> the `0.4.0` branch is now materially cleaner at runtime than the first RC rerun state, even though the next benchmark gains still depend on fixing remaining task-level capability misses such as `custom_v8_005`
