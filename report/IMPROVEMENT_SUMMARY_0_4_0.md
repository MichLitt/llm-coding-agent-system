# Improvement Summary 0.4.0

> Date: 2026-03-11
> Version: 0.4.0
> Scope: final accepted summary across runtime hardening, loop refactor, and same-code baseline reset

---

## Positioning

`0.4.0` is the version where Coder-Agent stops describing itself through historical benchmark wins and starts describing itself through fresh same-code artifacts.

The important shift is not only that the branch has new numbers. It is that the runtime, the benchmark loop, and the public docs are now aligned to the same code snapshot.

---

## Key Changes Relative to 0.3.0

### 1. Runtime hardening

`0.4.0` folds in the post-`0.3.0` runtime fixes that removed several release-blocking problems:

- recoverable handling for known tool failures instead of overusing hard `tool_exception`
- `read_file` line-range compatibility, including `min_lines`
- per-event-loop async client lifecycle cleanup
- stronger failure classification from combined `STDOUT` + `STDERR`
- better retry guidance for pytest collection failures, assertion failures, and API/signature mismatches
- shell command output decoding that no longer crashes on non-UTF-8 bytes

These changes moved the branch away from avoidable runtime failures and toward capability-limited failures that are easier to reason about.

### 2. `agent_loop` modular refactor

The runtime loop is no longer concentrated in one oversized implementation file.

`0.4.0` extracts the main orchestration helpers into:

- `coder_agent/core/agent_run_context.py`
- `coder_agent/core/agent_turns.py`
- `coder_agent/core/agent_tool_batch.py`

`coder_agent/core/agent_loop.py` now acts as the orchestration entrypoint instead of carrying all run-context, turn-parsing, retry, and verification behavior inline.

### 3. Benchmark source-of-truth reset

`0.3.0` still intentionally anchored its public story to historical promoted numbers.

`0.4.0` replaces that with final accepted same-code artifacts:

- HumanEval: `humaneval_040_final_c3`, `humaneval_040_final_c6`
- Custom standalone: `custom_040_final_c4`
- Custom compare: `custom_040_final_cmp_C3`, `custom_040_final_cmp_retry_C4`, `custom_040_final_cmp_retry_C6`

README and the new baseline report now point at these final artifacts instead of the old RC or `v8`/`v9` tables.

---

## Final Baseline Table

| Domain | Primary artifact | Result | Interpretation |
|--------|------------------|--------|----------------|
| HumanEval | `humaneval_040_final_c6` | `161/164 = 98.2%` | promoted primary baseline |
| HumanEval | `humaneval_040_final_c3` | `157/164 = 95.7%` | supporting comparison reference |
| Custom | `custom_040_final_cmp_retry_C6` | `21/21 = 100.0%` | promoted final compare baseline |
| Custom | `custom_040_final_cmp_C3` | `20/21 = 95.2%` | supporting clean compare reference |
| Custom | `custom_040_final_cmp_retry_C4` | `20/21 = 95.2%` | supporting memory-enabled compare reference |
| Custom | `custom_040_final_c4` | `19/21 = 90.5%` | standalone memory-enabled reference |

Important note:

- `custom_040_final_cmp_retry_C4` and `custom_040_final_cmp_retry_C6` supersede the polluted `C4`/`C6` lanes from the first `custom_040_final_cmp` compare attempt and are the accepted final compare artifacts.

---

## Validation Status

The final `0.4.0` local gate passed:

- `uv run pytest` -> `60/60`
- `uv run python -m coder_agent --help`
- default REPL startup with `/exit`

The accepted benchmark artifacts were generated from git commit `c372ec8`.

---

## Current Limitations

The most important remaining limitations after the final `0.4.0` closure are:

1. provider/API instability is still an operational risk in long compare runs, even though the retry artifacts recovered the final public baseline
2. Custom still has a concentrated residual capability gap on `custom_hard_003`
3. HumanEval still has residual task-level misses, most notably `HumanEval_108`, `HumanEval_130`, and `HumanEval_145` on the strongest final path
4. the standalone memory path still missed `custom_v8_005` in the final accepted rerun, and the correction-policy layer remains a natural simplification target

---

## Next Work Order

Recommended order after `0.4.0`:

1. provider-resilience work for `APIConnectionError` inside long compare runs
2. targeted capability work on `custom_hard_003`
3. targeted cleanup for residual HumanEval misses, especially `HumanEval_130` and `HumanEval_145`
4. a third-stage cleanup pass on retry/correction interfaces now that the main loop has been modularized

---

## Summary

`0.4.0` should be understood as:

> the first version where the runtime fixes, loop structure, benchmark artifacts, and public documentation all point at the same current-code baseline

That makes it a stronger release boundary than `0.3.0`, even though some next-step work has clearly shifted from architecture cleanup to provider resilience and targeted capability improvement.
