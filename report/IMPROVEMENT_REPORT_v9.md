# Improvement Report v9 - Parser Hardening and Eval Auto-Complete

> Date: 2026-03-10
> Scope: Runtime hardening only; no full HumanEval re-run

---

## Overview

v9 is an engineering-stability iteration, not a new benchmark-promotion cycle.

This round focused on:

1. hardening streamed tool-call parsing so malformed JSON no longer takes down the agent loop
2. adding eval-only auto-complete when external verification has already passed
3. validating the fixes with unit tests, targeted HumanEval spot checks, and Custom benchmark re-runs

Important constraint:

- no `C6` HumanEval smoke/full re-run was performed in v9
- the promoted HumanEval numbers therefore remain the v8 numbers

---

## Stage 1: Tool-Call Parsing Hardening

### Problem

In v8, one remaining system failure was a `JSONDecodeError` in `llm_client.py` while parsing streamed tool-call arguments. That failure class is not a model-capability miss; it is a runtime robustness issue.

### Implementation

`LLMClient.chat()` now:

- attempts direct JSON parsing first
- falls back to extracting the first balanced `{...}` object from noisy streamed arguments
- records malformed tool calls into `parse_errors`
- never raises a parsing exception back into the main agent loop for this path

`Agent._loop()` now:

- treats malformed tool calls as recoverable feedback
- does not execute the malformed tool call
- feeds the parse error back into context so the model can re-issue the tool call correctly

### Result

The tool-call parse failure path is now unit-tested and no longer manifests as an unhandled loop exception in the targeted validation run.

---

## Stage 2: Eval Auto-Complete on Verification Pass

### Problem

The v8 bottleneck had shifted from pure benchmark correctness to stop-time discipline:

- some tasks already passed external verification
- but the agent still kept taking steps and could end in `max_steps`

### Implementation

v9 adds a new termination path:

- `termination_reason = "verification_passed"`

Behavior:

- benchmark runs now opportunistically call the task-specific verifier after successful `write_file` / `run_command` steps
- if verification already passes, the run ends immediately and cleanly
- stop-time verification gating remains enforced only for `C6`
- auto-complete is available across benchmark runs even when stop-time gating is not

This splits two concerns cleanly:

- **auto-complete**: close the run early when the code is already good
- **stop-time gate**: block a final answer unless verification passes

---

## Stage 3: Validation

### Test Suite

Command:

```bash
uv run pytest
```

Result:

- **29/29 tests passed**

New coverage added for:

- balanced JSON extraction / noisy argument recovery
- malformed tool-call parse errors
- eval auto-complete success path
- separation of stop-time gating vs auto-complete in the eval runner

### HumanEval Spot Checks

Command:

- run the targeted C6 spot-check driver against `HumanEval_32`, `HumanEval_41`, `HumanEval_130`, and `HumanEval_134`

Result:

| Task | benchmark_passed | clean | termination_reason | steps |
|------|------------------|-------|--------------------|-------|
| `HumanEval_32` | yes | yes | `verification_passed` | 1 |
| `HumanEval_41` | yes | yes | `verification_passed` | 1 |
| `HumanEval_130` | yes | yes | `verification_passed` | 1 |
| `HumanEval_134` | yes | yes | `verification_passed` | 1 |

Interpretation:

- the targeted `max_steps` clean-stop gap is closed for the three benchmark-passing tasks
- the old `JSONDecodeError` path did not reappear on `HumanEval_134`

### Custom Re-Runs

#### Standalone `C4` full run

Command:

```bash
uv run python -m coder_agent eval --benchmark custom --preset C4 --config-label custom_full_c4_v9
```

Result:

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|--------|----------------|------------------|----------------|-----------|------------|
| `C4` | 85.7% (18/21) | 85.7% (18/21) | 85.7% (18/21) | 5.5 | 410 |

#### Clean `C4/C5/C6` comparison re-run

Command:

```bash
uv run python -m coder_agent eval --benchmark custom --compare C4,C5,C6 --config-label custom_cmp_v9
```

Result:

| Config | Benchmark Pass | Clean Completion | Strict Success | Partial Credit | Avg Steps | Avg Tokens |
|--------|----------------|------------------|----------------|----------------|-----------|------------|
| `C4` | 90.5% (19/21) | 90.5% (19/21) | 90.5% (19/21) | 90.5% | 6.2 | 410 |
| `C5` | 90.5% (19/21) | 90.5% (19/21) | 90.5% (19/21) | 90.5% | 5.7 | 410 |
| `C6` | 90.5% (19/21) | 90.5% (19/21) | 90.5% (19/21) | 92.9% | 5.2 | 410 |

### Interpretation

Two different conclusions emerged from v9 validation:

1. **The engineering fixes worked** on the targeted runtime/termination issues.
2. **Custom benchmark scores were unstable and below the promoted v8 baseline.**

So v9 improves runtime robustness, but it does **not** justify promoting new public best-result numbers.

---

## Summary

What v9 accomplished:

- removed the streamed tool-call parse crash from the targeted path
- added a clean `verification_passed` termination mode
- closed the targeted HumanEval clean-stop gap in spot checks
- expanded regression coverage from 22 tests to 29 tests

What v9 did **not** accomplish:

- no full HumanEval re-validation
- no new promoted benchmark tables
- no evidence that Custom benchmark performance improved over v8

Therefore:

- keep v8 as the promoted benchmark baseline in the README
- treat v9 as a runtime-hardening report
- if new public metrics are needed later, run a fresh dedicated benchmark cycle instead of reusing v9 engineering-validation runs
