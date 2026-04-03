# Improvement Report v0.4.1 - Ablation Experiment Framework

> Date: 2026-04-01
> Scope: ablation experiment infrastructure — feature contribution analysis for C1–C6 presets

---

## Overview

v0.4.1 adds an ablation experiment framework that quantifies the contribution of each individual
feature in the C1–C6 configuration sequence.

It does not introduce new agent capabilities, change any existing CONFIG_PRESETS, or start a new
benchmark promotion cycle. The 0.4.0 benchmark baselines (HumanEval C6=98.2%, Custom C6=100%)
remain the current source of truth.

The motivation for this round: the six presets C1–C6 already form a natural feature-addition
sequence, but there was no automated tooling to answer the question "how much does each feature
contribute to benchmark performance?" This framework provides that answer via two delta views:

- **Marginal**: each config compared against its direct predecessor (isolated feature contribution)
- **Cumulative**: each config compared against the C1 all-off baseline (total accumulated benefit)

A key design requirement was confirmed before implementation: all five config flags (`correction`,
`memory`, `checklist`, `verification_gate`, `planning_mode`) are independently consumed via
isolated `.get()` calls with no cross-flag dependencies. Toggling one flag cannot interfere with
another, making the ablation results interpretable without confounding interactions.

---

## Stage 1: New `coder_agent/eval/ablation.py`

The core module. An orchestration layer that wraps `EvalRunner.compare_configs()` — it does
not duplicate any task-execution, checkpointing, or metric-aggregation logic.

### PRESET_SEQUENCE, FEATURE_ADDED, MARGINAL_PAIRS

Three module-level constants define the entire experiment design:

```python
PRESET_SEQUENCE = ("C1", "C2", "C3", "C4", "C5", "C6")

FEATURE_ADDED = {
    "C1": "baseline",
    "C2": "+planning=react",
    "C3": "+correction",
    "C4": "+memory",
    "C5": "+checklist",
    "C6": "+verification_gate",
}

MARGINAL_PAIRS = (
    ("C2", "C1"),   # +planning=react
    ("C3", "C2"),   # +correction
    ("C4", "C3"),   # +memory
    ("C5", "C3"),   # +checklist (branches from C3, not C4)
    ("C6", "C3"),   # +verification_gate (branches from C3, not C4)
)
```

C5 and C6 both branch from C3 rather than C4, because they each add one orthogonal feature
(checklist and verification_gate respectively) to the correction+react foundation. This is the
correct isolation: comparing C5 vs C4 would conflate the checklist effect with the removal of
memory.

**Extensibility**: to add a new preset (e.g. C7), add one entry to each constant and one entry
to `CONFIG_PRESETS` in `factory.py`. No other code in this module needs to change.

### FeatureDelta dataclass

Stores the result of a single (config, reference) comparison:

- `config`, `reference`: the two configs being compared
- `feature_added`: human-readable label from `FEATURE_ADDED`
- `benchmark_pass_rate_delta`: config metric − reference metric (positive = feature helps)
- `strict_success_rate_delta`, `efficiency_score_delta`, `retry_cost_delta`, `avg_steps_delta`
- `config_benchmark_pass_rate`, `reference_benchmark_pass_rate`: raw values for context

### compute_feature_deltas()

Pure function. Input: `ComparisonReport`. Output: `list[FeatureDelta]`. No I/O.

Two modes via keyword argument:
- `mode="marginal"` iterates over `MARGINAL_PAIRS`, building one delta per pair
- `mode="cumulative"` iterates over `PRESET_SEQUENCE`, comparing each config against C1

Missing configs are silently skipped in both modes, making partial runs (e.g. `--limit 3`)
safe to analyze without raising errors.

### print_delta_table()

Prints a formatted table with columns: Config | vs | Feature | BenchΔ | StrictΔ | EffΔ |
RetryCostΔ | AvgStepsΔ. Mirrors the style of `metrics.print_metrics_table`.

### write_ablation_report()

Writes `{output_dir}/IMPROVEMENT_REPORT_{version}.md` containing:
- Ablation matrix table (C1–C6 feature flags)
- Full metric results table (all 6 configs)
- Marginal delta table
- Cumulative delta table
- Interpretation paragraph
- Summary

### AblationRunner class

```python
class AblationRunner:
    def __init__(self, eval_runner: EvalRunner) -> None: ...

    def run(
        self,
        tasks: list[TaskSpec],
        preset_configs: dict[str, dict],   # CONFIG_PRESETS from factory.py
        *,
        presets: tuple[str, ...] = PRESET_SEQUENCE,
        report_label: str = "ablation",
        benchmark_name: str = "",
        resume: bool = False,
        verbose: bool = True,
    ) -> ComparisonReport: ...
```

The entire body of `run()` is a 5-line adapter that selects the requested presets from
`preset_configs` and delegates to `EvalRunner.compare_configs()`. The `presets` parameter
allows running a subset (e.g. `presets=("C1","C3","C6")`) for faster exploratory runs.

---

## Stage 2: New `coder_agent/cli/run_ablation.py`

Standalone CLI entrypoint. Invoked as:

```bash
uv run python -m coder_agent.cli.run_ablation --benchmark custom
uv run python -m coder_agent.cli.run_ablation --benchmark humaneval --limit 20
uv run python -m coder_agent.cli.run_ablation --presets C1,C3,C6
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--benchmark` | `custom` | `custom` or `humaneval` |
| `--presets` | C1,C2,C3,C4,C5,C6 | Comma-separated subset of presets |
| `--output` | `cfg.eval.output_dir` | Results JSON directory |
| `--report-dir` | `<root>/report/` | Markdown report output directory |
| `--limit` | 0 (all) | Max tasks per config |
| `--resume` | off | Resume from checkpoints |
| `--version` | v0.4.1 | Report filename version suffix |

The module is not registered on the main CLI group — it is a standalone entrypoint for
batch ablation runs. Unknown preset names are rejected at startup with a clear error message.

**No changes were made to `factory.py` or any existing module.** C1–C6 are used directly
from the existing `CONFIG_PRESETS` dict.

---

## Stage 3: Validation

### Test Suite

Command:

```bash
uv run pytest tests/test_ablation.py -v
uv run pytest
```

New test file: `tests/test_ablation.py` — 17 tests covering:

| Group | Tests | Coverage |
|-------|-------|---------|
| Constants | 3 | PRESET_SEQUENCE, FEATURE_ADDED, MARGINAL_PAIRS consistency |
| compute_feature_deltas (marginal) | 5 | count, delta values, C5/C6 branch from C3, missing configs |
| compute_feature_deltas (cumulative) | 5 | count, baseline reference, delta values, KeyError, ValueError |
| AblationRunner | 4 | compare_configs delegation, config keys, subset, unknown preset skip |
| write_ablation_report | 2 | file creation, header content |

Target: 108 existing + 17 new = **125 total tests**.

### CLI Smoke Check

```bash
uv run python -m coder_agent.cli.run_ablation --help
```

Verifies import chain and Click option registration without running any tasks.

---

## Interpretation

v0.4.1 is a research infrastructure release. It adds no new agent capabilities and does not
alter the 0.4.0 benchmark baselines.

What this report demonstrates:

1. The five config flags are independently implemented — ablation results are interpretable
   without confounding interactions between features
2. `MARGINAL_PAIRS` correctly models the C5/C6 branching structure (both branch from C3)
3. The framework reuses 100% of existing run/checkpoint/metric machinery from `EvalRunner`
4. Adding a new feature (C7) requires exactly three lines across two files

What v0.4.1 does **not** demonstrate:

- No actual benchmark results (requires an LLM API key and significant compute time)
- No new agent capabilities or pass rate changes
- No changes to any existing module outside the three new files

The practical outcome is:

> the project now has automated tooling to run the full C1–C6 ablation matrix against any
> benchmark, compute both marginal and cumulative feature contribution deltas, and write a
> structured markdown report — ready for use once the LLM API is available

---

## Summary

v0.4.1 should be understood as a framework and tooling report, not a new baseline report.

It accomplished:

- new `coder_agent/eval/ablation.py` (~230 lines): PRESET_SEQUENCE, MARGINAL_PAIRS,
  FeatureDelta, AblationRunner, compute_feature_deltas (marginal + cumulative),
  print_delta_table, write_ablation_report
- new `coder_agent/cli/run_ablation.py` (~120 lines): standalone CLI with benchmark/presets/
  limit/resume options; validates preset names at startup
- new `tests/test_ablation.py` (17 tests): full coverage of constants, delta computation,
  AblationRunner delegation, and report generation
- zero modifications to any existing file

The 0.4.0 benchmark baselines remain the current source of truth. v0.4.1 provides the tooling
to extend them with rigorous feature contribution analysis.
