# Eval Comparison Report

## Summary / 摘要

This report summarizes the currently available comparison runs.

本报告基于当前已经完整落盘的对比实验结果，不等待新的全量重跑。

Included comparison groups:

- `custom_cmp_C1` to `custom_cmp_C4` on the full custom suite (`11` tasks)
- `humaneval_cmp_C1` to `humaneval_cmp_C4` on a HumanEval subset (`5` tasks)

Important scope note:

- `custom` compare is full-suite
- `humaneval` compare is still a `5`-task subset, not the full HumanEval benchmark

## Experiment Matrix / 实验矩阵

Commands used:

```bash
python -m coder_agent eval --benchmark custom --compare C1,C2,C3,C4 --config-label custom_cmp
python -m coder_agent eval --benchmark humaneval --limit 5 --compare C1,C2,C3,C4 --config-label humaneval_cmp
python -m coder_agent analyze custom_cmp
python -m coder_agent analyze humaneval_cmp
```

Config intent:

- `C1`: direct generation, no correction, no memory
- `C2`: react, no correction, no memory
- `C3`: react, correction enabled, no memory
- `C4`: react, correction enabled, memory enabled

Interpretation note:

- these are useful engineering configurations
- they are not yet a clean causal ablation, because not every switch is fully wired into distinct runtime behavior yet

## Result Tables / 结果表

### Custom full suite (`11` tasks)

The stored comparison JSON still uses the older summary schema, so the table below follows the current benchmark-first interpretation reconstructed from results plus trajectories.

| Config | Benchmark Pass | Clean Completion | Strict Success | Partial Credit | Retry Cost | Avg Steps |
|---|---:|---:|---:|---:|---:|---:|
| `custom_cmp_C1` | 81.8% | 63.6% | 63.6% | 86.4% | 4.2% | 10.5 |
| `custom_cmp_C2` | 90.9% | 72.7% | 72.7% | 95.5% | 4.9% | 11.0 |
| `custom_cmp_C3` | 81.8% | 63.6% | 63.6% | 86.4% | 4.8% | 10.5 |
| `custom_cmp_C4` | 90.9% | 63.6% | 63.6% | 95.5% | 1.8% | 10.5 |

Observed leaders:

- Best benchmark pass: tie between `C2` and `C4`
- Best clean completion: `C2`
- Best strict success: `C2`
- Lowest retry cost: `C4`

### HumanEval subset (`5` tasks)

This is a subset comparison, not a full-benchmark claim.

| Config | Benchmark Pass | Clean Completion | Strict Success | Partial Credit | Retry Cost | Avg Steps |
|---|---:|---:|---:|---:|---:|---:|
| `humaneval_cmp_C1` | 100.0% | 20.0% | 20.0% | 100.0% | 0.0% | 3.8 |
| `humaneval_cmp_C2` | 100.0% | 0.0% | 0.0% | 100.0% | 0.0% | 3.6 |
| `humaneval_cmp_C3` | 80.0% | 20.0% | 20.0% | 80.0% | 0.0% | 3.0 |
| `humaneval_cmp_C4` | 100.0% | 60.0% | 60.0% | 100.0% | 0.0% | 3.6 |

Observed leaders:

- Best benchmark pass: tie between `C1`, `C2`, and `C4`
- Best clean completion: `C4`
- Best strict success: `C4`

## Cross-config Findings / 配置对比结论

### Custom findings / Custom 结论

1. `C2` is the strongest overall configuration on the current custom suite.
   在当前 `custom` 全量 `11` 题上，`C2` 在 `Benchmark Pass`、`Clean Completion`、`Strict Success` 三个核心维度上都最均衡。

2. `C4` improves workflow stability more than end-state completion.
   `C4` 的优势主要体现在更低的 `Retry Cost`，说明 memory 至少在降低重复试错上有帮助，但目前还没有把 clean completion 推到超过 `C2`。

3. `C3` does not currently outperform `C2`.
   这说明现在的 correction 开关还没有稳定转化为更好的整体结果，至少在当前实现里还不是一个强增益项。

### HumanEval findings / HumanEval 结论

1. The main gap is completion quality, not benchmark capability.
   在这 `5` 题子集上，`C1`、`C2`、`C4` 都能达到 `100%` benchmark pass，但 clean completion 差距很大，说明主要问题是 agent 结束质量，不是题目本身做不出来。

2. `C4` is the best strict configuration on the current subset.
   `C4` 在 HumanEval 子集上的 `Clean Completion` 和 `Strict Success` 都明显领先，因此当前最适合拿来做 demo。

3. `C2` is a warning sign.
   `C2` 在该子集上虽然 benchmark 全过，但 `Clean Completion = 0%`，这类结果说明只看 benchmark 通过率会掩盖 workflow-level instability。

## Failure Taxonomy Notes / 失败分类观察

### Custom

Current custom failures are mixed:

- genuine benchmark misses still appear on harder tasks
- several tasks pass all checks but end in `failed` or `timeout`
- the dominant issue is no longer only code correctness
- workflow termination and step-budget control are now first-order problems

### HumanEval subset

Current HumanEval subset failures are shallower:

- most failures are not missing-function failures anymore
- the harness itself is working
- the gap is mostly between "the implementation passes" and "the agent exits cleanly"

## Interpretation / 解读

### What we can say with confidence / 当前可以比较有把握地说

- The eval stack now supports meaningful config comparison.
- `custom` already provides a credible full-suite comparison baseline.
- `humaneval` subset results are useful for trend reading and demo selection.
- Benchmark-first semantics are necessary; old strict-only reading was misleading.

### What we should not overclaim / 当前不该过度宣称的部分

- This is not a full HumanEval comparison.
- This is not yet a clean research-grade ablation.
- `C1/C2/C3/C4` trends are operationally useful, but not yet sufficient for strong causal claims.

## Limitations / 局限

1. `humaneval_cmp` is only a `5`-task subset.
2. Some compare artifacts were generated before the new result schema existed, so benchmark/clean/strict splits are reconstructed rather than natively stored.
3. The interrupted `humaneval_full_batch` run means there is still no completed full-benchmark HumanEval baseline.
4. `planning_mode` and `correction` are still not fully separated as true execution behaviors.

## Recommended Next Step / 下一步建议

1. If the goal is demo quality, use `C2` as the current custom default and `C4` as the current HumanEval-subset default.
2. If the goal is research quality, the next step is to make `C1/C2/C3/C4` truly behavior-distinct before re-running the comparison matrix.
3. If the goal is evaluation credibility, the next highest-value task is a completed HumanEval full-batch baseline.
