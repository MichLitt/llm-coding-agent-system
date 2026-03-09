# Improvement Report v8 - Engineering Cleanup, C6 Re-Run, and ImportError Guidance

> Date: 2026-03-09
> Scope: Public repo hardening plus one focused C6 validation cycle

---

## Overview

v8 follows the planned execution order exactly:

1. Add the minimum public-repo engineering layer: `MIT` license and GitHub Actions CI
2. Re-run `C6` with current code, first as smoke, then full HumanEval
3. Tighten `ImportError` self-correction guidance without changing the overall ReAct architecture
4. Prepare the next benchmark-expansion step by enlarging the Custom task suite

This round does not introduce a new preset. It strengthens the repo and re-validates the current best configuration.

---

## Stage 1: Public Repo Engineering

### What changed

- Added `LICENSE` with the `MIT` text
- Added `.github/workflows/ci.yml`
- CI now runs:
  - `uv sync`
  - `uv run pytest`
  - `uv run python -m coder_agent --help`
- README was updated to:
  - point to the latest report
  - mention the license
  - state that raw runtime artifacts are not committed
  - clarify that GitHub Actions uses Python 3.12

### Why this matters

The repo is now closer to a standard public engineering project rather than a local experiment folder:

- licensing is explicit
- the install/test/help path is automated
- public documentation stays aligned with the current public result

---

## Stage 2: C6 Re-Validation

### Smoke run

Command:

```bash
uv run python -m coder_agent eval --benchmark humaneval --limit 10 --preset C6 --config-label humaneval_smoke_c6_v8
```

Result:

| Run | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|-----|----------------|------------------|----------------|-----------|------------|
| `humaneval_smoke_c6_v8` | 100.0% (10/10) | 100.0% (10/10) | 100.0% (10/10) | 3.00 | 410 |

This confirmed that the post-v7 code path was stable enough to continue to the full run.

### Full run

Command:

```bash
uv run python -m coder_agent eval --benchmark humaneval --preset C6 --resume --config-label humaneval_full_c6_v8
```

Result:

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|--------|----------------|------------------|----------------|-----------|------------|
| C6 v7 baseline | 97.0% (159/164) | 97.0% (159/164) | 97.0% (159/164) | 3.33 | 410 |
| **C6 v8** | **98.2% (161/164)** | 96.3% (158/164) | 96.3% (158/164) | **3.49** | 410 |

### Interpretation

v8 improved the primary benchmark metric:

- Benchmark Pass: **159 -> 161**

But it also exposed a stricter termination-quality regression:

- Clean Completion: **159 -> 158**
- Strict Success: **159 -> 158**

This means the system got better at producing benchmark-passing code, but not always at deciding when to stop.

---

## Stage 3: ImportError Guidance Improvement

### Motivation

In v7, `HumanEval_137` failed as:

- `termination_reason = retry_exhausted`
- root cause: repeated `ImportError`

The previous guidance was too blunt:

> missing module -> try `pip install`

That mixes together two different cases:

- real third-party dependency problems
- project-local import mistakes or bad package paths

### Implementation change

`agent.py` now builds `ImportError` guidance from traceback context:

- extract the missing module name
- extract the file that triggered the import
- inspect the workspace for local module candidates
- prefer code/path fixes when the missing module looks project-local
- only suggest `pip install` when the module looks external and no local candidate exists
- if the same `ImportError` repeats, escalate the hint instead of repeating the same advice

### Observable effect

`HumanEval_137` changed from failure to success:

| Task | v7 | v8 |
|------|----|----|
| `HumanEval_137` | `retry_exhausted`, benchmark fail | `model_stop`, benchmark pass |

So the new guidance materially helped one of the previously actionable failure modes.

---

## Stage 4: Failure Shape After v8

Termination breakdown for `humaneval_full_c6_v8`:

| termination_reason | Count |
|-------------------|-------|
| `model_stop` | 158 |
| `max_steps` | 5 |
| `loop_exception` | 1 |

Remaining non-clean tasks:

| Task | benchmark_passed | termination_reason | Meaning |
|------|------------------|--------------------|---------|
| `HumanEval_32` | yes | `max_steps` | solution passes, but agent fails to stop cleanly |
| `HumanEval_41` | yes | `max_steps` | same as above |
| `HumanEval_93` | no | `max_steps` | real logic failure |
| `HumanEval_130` | yes | `max_steps` | solution passes, but agent fails to stop cleanly |
| `HumanEval_134` | no | `loop_exception` | `JSONDecodeError` in `llm_client.py` tool-call parsing |
| `HumanEval_145` | no | `max_steps` | real logic failure |

### What improved relative to v7

- `HumanEval_135` recovered from `loop_exception` to success
- `HumanEval_136` recovered from `loop_exception` to success
- `HumanEval_137` recovered from `retry_exhausted` to success

### What regressed relative to v7

- `HumanEval_32`, `HumanEval_41`, and `HumanEval_130` now pass benchmark checks but still terminate as `max_steps`
- `HumanEval_93` regressed from success to failure

### Main conclusion

v8 moved the frontier forward on benchmark correctness, but it revealed that the next bottleneck is now:

> stopping discipline after the code is already good enough

That is different from the earlier self-evaluation failure pattern, and different again from the old streaming bug.

---

## Stage 5: Custom Benchmark Expansion and First 21-Task Results

The built-in Custom task suite was expanded from 11 to 21 tasks by adding 10 higher-discrimination tasks focused on:

- multi-file refactors
- CLI/config handling
- dependency-graph style reasoning
- package/import repair
- mocked API integration
- async workflow behavior

This stage is deliberately preparatory:

- the tasks are added to the benchmark suite now
- the suite is harder and more diagnostic than the old saturated 11-task version

### First comparison on the 21-task suite

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Retry Cost |
|--------|----------------|------------------|----------------|-----------|------------|
| **C4** | **100.0% (21/21)** | **95.2% (20/21)** | **95.2% (20/21)** | 7.95 | 8.4% |
| C5 | 90.5% (19/21) | 85.7% (18/21) | 85.7% (18/21) | **7.33** | 8.4% |
| C6 | 95.2% (20/21) | 90.5% (19/21) | 90.5% (19/21) | 8.10 | **4.7%** |

### Interpretation

- `C4` remains the strongest Custom configuration when the benchmark gets harder
- `C5` still improves step efficiency, but its correctness advantage from the old 11-task suite does not survive the expanded task set
- `C6` lowers retry cost, but does not beat `C4` on benchmark pass or strict success

Observed failures:

- `C4`: only `custom_hard_001`, which benchmark-passed but did not stop cleanly
- `C5`: `custom_hard_001`, `custom_hard_003`, and `custom_v8_005`
- `C6`: `custom_hard_001` and `custom_v8_005`

### Important caveat

During this comparison, `custom_v8_010` was hardened after repeated agent-generated test hangs:

- the benchmark now provides a fixed `test_async_jobs.py`
- the shell timeout path now kills the full subprocess tree on Windows

`C6` was re-run after that hardening. `C4` and `C5` retained successful runs on `custom_v8_010`, so the final ranking above is still meaningful, but the three configs were not all re-run from a perfectly identical pre-task state.

---

## Summary of v8 Changes

| Area | What changed |
|------|--------------|
| Public repo engineering | Added `MIT` license and GitHub Actions CI |
| HumanEval validation | Ran `C6` smoke and full re-validation |
| ImportError handling | Replaced generic install-first hint with traceback-aware guidance |
| Benchmark suite | Expanded Custom tasks from 11 to 21 and ran the first `C4/C5/C6` comparison |
| Public docs | Updated README to point to v8 as the latest report |

---

## Next Directions

1. Fix the `JSONDecodeError` path in `llm_client.py` so `HumanEval_134` is no longer a system failure
2. Improve termination discipline for tasks like `HumanEval_32`, `HumanEval_41`, and `HumanEval_130` that already pass benchmark verification but still hit `max_steps`
3. Tighten stop-time control on Custom tasks like `custom_hard_001` and `custom_v8_005`, where verification or task completion is reached but the agent still times out
4. Re-run a fully clean `C4/C5/C6` comparison on the hardened 21-task Custom suite if publication-grade apples-to-apples reporting is required
5. Only after that, decide whether the next major effort should target:
   - stronger stop-time control, or
   - deeper planning/decomposition upgrades
