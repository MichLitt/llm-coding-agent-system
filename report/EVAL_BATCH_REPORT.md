# Eval Batch Report

## Summary / 摘要

This report is based only on result artifacts that are complete and safe to interpret.

本报告只使用当前已经完整落盘、可以可靠解读的评测结果。

Included runs:

- `custom_full_batch` (`11` built-in custom tasks, complete)
- `humaneval_smoke_batch` (`3` HumanEval tasks, complete)

Excluded from formal metrics:

- `humaneval_full_batch` attempt
  - trajectory file exists: `trajectories/humaneval_full_batch.jsonl`
  - final result file is missing
  - the run was interrupted after about `146` recorded trajectories
  - therefore it is treated as an incomplete run and not used for quantitative conclusions

## Commands Run / 执行命令

```bash
python -m coder_agent eval --benchmark custom --config-label custom_full_batch
python -m coder_agent eval --benchmark humaneval --limit 3 --config-label humaneval_smoke_batch
```

## Result Snapshot / 结果快照

### Custom full batch

Source: `results/custom_full_batch.json`

| Metric | Value |
|---|---:|
| Tasks | 11 |
| Benchmark Pass | 90.9% |
| Clean Completion | 54.5% |
| Strict Success | 54.5% |
| Partial Credit | 95.5% |
| Avg Steps | 10.5 |
| Avg Tokens | 371 |

Per-task highlights:

- `benchmark failed`: `custom_hard_001` (`1/2` checks, `timeout`)
- `benchmark passed but not clean`: `custom_easy_002`, `custom_easy_003`, `custom_medium_005`, `custom_hard_003`
- `clean success`: `6 / 11`

### HumanEval smoke batch

Source: `results/humaneval_smoke_batch.json`

This run was produced before the benchmark-first result schema existed, so:

- `Benchmark Pass` is inferred from `checks_passed == checks_total`
- `Clean Completion` and `Strict Success` are read using the old `success` field

| Metric | Value |
|---|---:|
| Tasks | 3 |
| Benchmark Pass | 100.0% |
| Clean Completion | 66.7% |
| Strict Success | 66.7% |
| Partial Credit | 100.0% |
| Avg Steps | 3.0 |
| Avg Tokens | 371 |

Per-task highlights:

- `benchmark passed and clean`: `HumanEval_0`, `HumanEval_1`
- `benchmark passed but not clean`: `HumanEval_2`

## Key Findings / 关键结论

1. The `custom` benchmark is now strong at task completion, but not yet stable at clean agent termination.
   `custom` 全量任务的 benchmark 通过率已经达到 `90.9%`，但 clean completion 只有 `54.5%`，说明当前主要短板不是“不会做”，而是“做出来后收尾不稳定”。

2. The HumanEval harness is operational on real tasks.
   当前 `humaneval` smoke 的 `3/3` benchmark checks 都通过，说明 `solution.py -> extraction -> official test` 这条链路已经可用。

3. Benchmark-first reporting materially changes the interpretation.
   如果继续只看旧的 `success`，会低估真实 benchmark 表现；现在必须区分 `Benchmark Pass`、`Clean Completion`、`Strict Success`。

## Failure Cases / 失败样例

### `custom_hard_001`

- Result: real benchmark failure
- Checks: `1/2`
- Final status: `timeout`
- Interpretation:
  - this is a genuine task miss
  - the issue is not just reporting semantics

### `custom_easy_003`

- Result: benchmark passed
- Final status: `timeout`
- Interpretation:
  - the code and verification checks succeeded
  - the agent still failed to finish cleanly inside the step budget

### `HumanEval_2`

- Result: benchmark passed
- Final status under old schema: `success = false`
- Interpretation:
  - this is exactly the class of case that motivated the schema split
  - benchmark capability is present, but completion semantics are noisy

## Analysis / 分析

### What is reliable now / 当前可以信的部分

- `custom_full_batch` is a complete full-batch baseline for the built-in custom suite.
- `humaneval_smoke_batch` confirms the HumanEval harness works on real tasks.
- The distinction between benchmark result and agent completion is now visible and actionable.

### What is not reliable enough yet / 当前还不够强的部分

- There is no completed `humaneval` full-batch result yet.
- The interrupted `humaneval_full_batch` trajectory file is useful as a debugging artifact, but not as a reporting dataset.
- Some older result files still use the pre-refactor schema, so cross-run interpretation needs care.

### Practical reading / 实际解读

- For capability reporting:
  - prioritize `Benchmark Pass`
- For workflow-quality reporting:
  - use `Clean Completion`
- For strict end-to-end success:
  - use `Strict Success`

Under that reading, the current project state is:

- `custom`: already a meaningful full-batch baseline
- `humaneval`: pipeline works, but evidence is still smoke-level rather than full-benchmark-level

## Next Actions / 后续建议

1. Re-run `humaneval` full batch only when needed for a stronger benchmark claim.
2. Keep all future tables benchmark-first by default.
3. Prioritize agent termination quality next, because that is now the dominant gap on tasks that already pass verification.
