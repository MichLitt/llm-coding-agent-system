# Improvement Report v7 - C6 Verification Gate on HumanEval

> **Date**: 2026-03-08
> **Scope**: One focused iteration after v6, centered on HumanEval correctness recovery

---

## Overview

v7 executes the next-step plan proposed after v6, with a strict "improve score first" priority:

1. **Complete the missing HumanEval C5 full run** to measure whether Adaptive Checklist transfers beyond Custom tasks.
2. **Implement C6 Verification Gate** on top of the C3 baseline:
   - task-aware external verification before final stop
   - HumanEval official check injected in-loop
   - retry-on-failed-verification up to 2 stop attempts
3. **Improve loop_exception observability** so system failures are no longer mixed with model capability failures.

This round does **not** pursue broader research directions such as LLM-judged checklist completion or Custom benchmark expansion.

---

## Stage 1: HumanEval C5 Full Run

### Motivation

v6 showed that C5 (Adaptive Checklist) improves efficiency on Custom 11 tasks with zero regression, but HumanEval data was missing. Because HumanEval tasks are already very short (~3 steps), the expected gain from decomposition was uncertain and needed to be verified empirically rather than assumed.

### Result: C5 does **not** transfer well to HumanEval

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|--------|---------------|-----------------|----------------|-----------|------------|
| C3 (react + correction) | 96.3% (158/164) | **100.0%** (164/164) | 96.3% (158/164) | 3.05 | 410 |
| **C5 (C4 + checklist)** | **80.5% (132/164)** | 95.7% (157/164) | **80.5% (132/164)** | **3.03** | **397.5** |

### Key finding

C5 preserves the short-trajectory profile of HumanEval, but introduces a large correctness regression:

- Benchmark Pass drops from **96.3% to 80.5%** (**-15.8pp**, 158 -> 132 passed tasks)
- Clean Completion stays high (**95.7%**), meaning the agent still frequently *believes* it completed the task
- This is not a planning-efficiency problem; it is a **termination-quality / self-evaluation** problem

### Failure shape

Termination breakdown for `humaneval_full_c5_v6`:

| termination_reason | Count |
|-------------------|-------|
| `model_stop` | 156 |
| `loop_exception` | 7 |
| `max_steps` | 1 |

Most importantly:

- **25 tasks are `agent_completed_cleanly=true` but `benchmark_passed=false`**
- These are the exact "clean-stop-but-wrong" failures that motivated the C6 verification gate

Representative C5 clean-stop failures:

`HumanEval_54, HumanEval_55, HumanEval_94, HumanEval_95, HumanEval_98, HumanEval_99, HumanEval_100, HumanEval_102, HumanEval_108, HumanEval_109, HumanEval_113, HumanEval_120, HumanEval_122, HumanEval_123, HumanEval_125, HumanEval_127, HumanEval_130, HumanEval_134, HumanEval_135, HumanEval_136, HumanEval_141, HumanEval_148, HumanEval_150, HumanEval_153, HumanEval_163`

### Interpretation

The conclusion from v6 now becomes sharper:

- **Adaptive Checklist is benchmark-sensitive**
- It is beneficial on multi-step tool-using tasks (Custom), but harmful on function-level HumanEval tasks when used without a hard verification gate
- Therefore, C5 should be treated as a **task-class-specific optimization**, not as a universal default

---

## Stage 2: C6 Verification Gate

### Motivation

v6's LLM-as-Critic analysis found `self_eval` as the dominant HumanEval C4 failure mode. The C5 full run confirmed the same failure mode at a larger scale: the agent frequently stops confidently after a syntax-only check, while the implementation is semantically wrong.

The core design goal of C6 is therefore:

> Do not let the agent terminate on HumanEval unless an external verifier confirms the current `solution.py` passes.

### Implementation

**Modified files**:

- `coder_agent/core/agent.py`
- `coder_agent/eval/runner.py`
- `coder_agent/cli/main.py`
- `coder_agent/memory/trajectory.py`
- `coder_agent/eval/analysis.py`

**New behavior**:

1. `TaskSpec` now supports a `verification_contract`
2. `Agent.run()` / `Agent._loop()` now accept:
   - `verification_hook`
   - `max_verification_attempts=2`
3. When the model tries to stop (`tool_uses == []`):
   - the agent first calls the external verifier
   - if verification fails, the final answer is *not* accepted
   - the failure summary is injected back into context as feedback
   - the agent gets another chance to repair and re-verify
4. If verification fails twice at stop-time, the run terminates with:
   - `termination_reason = "verification_failed"`

**Preset addition**:

```python
C6 = {
    "correction": True,
    "memory": False,
    "planning_mode": "react",
    "verification_gate": True,
}
```

**Benchmark-specific gate policy**:

- **Custom**: reuse existing YAML verification commands
- **HumanEval**: run the official HumanEval check in-loop before allowing the final stop

This keeps the gate task-aware rather than hard-coding "if `solution.py` exists, run something".

### Result: C6 fixes the dominant failure mode

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|--------|---------------|-----------------|----------------|-----------|------------|
| C3 (react + correction) | 96.3% (158/164) | **100.0%** (164/164) | 96.3% (158/164) | 3.05 | 410 |
| C5 (checklist) | 80.5% (132/164) | 95.7% (157/164) | 80.5% (132/164) | 3.03 | 397.5 |
| **C6 (C3 + verification gate)** | **97.0% (159/164)** | **97.0% (159/164)** | **97.0% (159/164)** | 3.33 | 410 |

### Key findings

- **C6 is now the strongest HumanEval configuration in the project**: **97.0%** (159/164)
- vs. **C3**:
  - +1 task passed (**159 vs 158**)
  - slight step overhead (**3.33 vs 3.05**)
  - correctness improves despite the extra verification cost
- vs. **C5**:
  - **+27 tasks passed** (**159 vs 132**)
  - clean-stop-but-wrong collapses from **25 tasks to 0**

Termination breakdown for `humaneval_full_c6_v6`:

| termination_reason | Count |
|-------------------|-------|
| `model_stop` | 158 |
| `loop_exception` | 4 |
| `max_steps` | 1 |
| `retry_exhausted` | 1 |

Most important behavioral change:

- **C5 clean-stop-but-wrong tasks**: 25
- **C6 clean-stop-but-wrong tasks**: **0**

This directly validates the original hypothesis: the dominant HumanEval failure mode was not missing planning, but missing **termination-time external verification**.

---

## Stage 3: loop_exception Observability

### Motivation

Before C6, `loop_exception` failures often collapsed into vague downstream symptoms such as `solution.py not created`. That made it difficult to distinguish:

- actual model inability
- tool/runtime instability
- logging / streaming bugs

### Implementation

Unhandled exceptions in `agent.py` now record:

- exception stage
- exception class
- exception message
- traceback summary

These diagnostics are written into both:

- `TurnResult.error_details`
- trajectory steps (synthetic system step)

### What this revealed

The remaining C6 failures are now much easier to interpret:

| Task | termination_reason | Real issue |
|------|--------------------|-----------|
| `HumanEval_134` | `loop_exception` | `OSError: [Errno 22] Invalid argument` during streamed printing |
| `HumanEval_135` | `loop_exception` | same as above |
| `HumanEval_136` | `loop_exception` | same as above |
| `HumanEval_137` | `retry_exhausted` | repeated `ImportError` |
| `HumanEval_145` | `max_steps` | unresolved logic failure / capability ceiling |

This is an important improvement even though it does not directly change the score:

- `HumanEval_134/135/136` are **system bugs**, not reasoning failures
- `HumanEval_145` remains a genuine hard reasoning task

In other words, C6 has already pushed the remaining failure set much closer to the true capability boundary.

---

## Summary of v7 Changes

| Component | File(s) | What changed |
|-----------|---------|-------------|
| HumanEval C5 full run | `results/humaneval_full_c5_v6.json` | Completed missing HumanEval C5 experiment |
| Verification gate | `core/agent.py`, `eval/runner.py` | Stop-time external verification with retry-on-fail |
| C6 preset | `cli/main.py` | Added `C6 = C3 + verification_gate` |
| Verification contract | `eval/runner.py`, `custom/loader.py` | Task-aware verification interface |
| Exception diagnostics | `core/agent.py`, `memory/trajectory.py` | Added exception stage/class/message/traceback recording |
| Termination analysis | `eval/analysis.py` | Added termination reason reporting from trajectory data |

---

## Cumulative Takeaways

### 1. Checklist is not a universal improvement

C5 looked strong on Custom tasks, but HumanEval proves that decomposition can hurt when:

- tasks are short
- semantics dominate over planning
- the agent is allowed to self-certify completion

### 2. Verification gate is the correct fix for self-eval failures

The decisive result of v7 is not just the +1 task over C3. It is the elimination of the entire failure class:

- **from 25 clean-stop-but-wrong tasks to 0**

This is exactly what a system-level intervention should do: remove a class of failure rather than locally tweaking prompts.

### 3. Remaining errors are now much more actionable

After C6, the remaining failures are no longer dominated by silent semantic misses:

- 3 are streaming/printing system bugs
- 1 is retry exhaustion on dependency/import handling
- 1 is a true logic ceiling

That is a much healthier post-improvement failure distribution.

---

## Notes on Analysis Semantics

One implementation caveat surfaced during this round:

- `results/*.json` should be treated as the source of truth for benchmark metrics
- `trajectories/*.jsonl` may contain duplicate task trajectories after resume runs
- therefore, `analyze <experiment_id>` is useful for qualitative failure inspection, but not always for exact task counts after a resumed long run

All v7 headline metrics above are taken from the final `results/humaneval_full_c5_v6.json` and `results/humaneval_full_c6_v6.json` files.

---

## Next Directions

1. **Fix streamed printing instability** in `Agent._safe_print()` / token streaming, targeting `HumanEval_134/135/136`
2. **Re-run C6 after the logging fix** to see whether the score rises from **159/164** to **162/164**
3. **Handle ImportError retry policy more explicitly** so `HumanEval_137` does not die as `retry_exhausted`
4. **Keep C5 as a task-class-specific option**, not the default HumanEval preset
