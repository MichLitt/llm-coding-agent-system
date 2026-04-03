# Ablation Report v0.4.4-debug

> Date: 2026-04-02
> Benchmark: custom
> Scope: feature ablation study — contribution of each C1–C6 component

---

## Overview

This report quantifies the contribution of each individual feature in the C1–C6
configuration sequence by computing metric deltas between consecutive presets
(marginal view) and against the C1 all-off baseline (cumulative view).

---

## Stage 1: Ablation Matrix

| Preset | correction | memory | checklist | verification_gate | planning_mode | Feature Added |
|--------|-----------|--------|-----------|-------------------|---------------|---------------|
| C1 | False | False | False | False | direct | baseline |
| C2 | False | False | False | False | react | +planning=react |
| C3 | True | False | False | False | react | +correction |
| C4 | True | True | False | False | react | +memory |
| C5 | True | False | True | False | react | +checklist |
| C6 | True | False | False | True | react | +verification_gate |

---

## Stage 2: Metric Results

| Config | N | BenchPass | CleanComplete | StrictSuccess | Efficiency | RetryCost | AvgSteps | AvgTokens |
|--------|---|-----------|---------------|---------------|------------|-----------|----------|-----------|
| C3 | 1 | 0.0% | 0.0% | 0.0% | 0.0000 | 0.0% | 2.0 | 756 |

---

## Stage 3: Feature Contribution Deltas

### Marginal Deltas (each config vs its direct predecessor)

Delta = config_metric − reference_metric. Positive = feature improves the metric.

| Config | vs | Feature | BenchΔ | StrictΔ | EfficiencyΔ | RetryCostΔ | AvgStepsΔ |
|--------|----|---------|----|---------|-------------|------------|-----------|

### Cumulative Deltas (each config vs C1 baseline)

| Config | vs | Feature | BenchΔ | StrictΔ | EfficiencyΔ | RetryCostΔ | AvgStepsΔ |
|--------|----|---------|----|---------|-------------|------------|-----------|

---

## Interpretation

The marginal delta table shows the isolated contribution of each feature when added
to the preceding configuration. C5 and C6 both branch from C3 (not C4) because they
each add one orthogonal feature — checklist and verification_gate respectively — to
the correction+react foundation.

The cumulative delta table shows the total accumulated benefit of each preset relative
to the C1 all-off baseline.

---

## Summary

Ablation study completed on 2026-04-02. Benchmark: custom.
Base framework: C1–C6 CONFIG_PRESETS from factory.py (no new presets added).
To extend: add a new preset to CONFIG_PRESETS and append entries to
PRESET_SEQUENCE, FEATURE_ADDED, and MARGINAL_PAIRS in ablation.py.
