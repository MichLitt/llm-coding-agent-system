# Ablation Report v0.4.4

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
| C1 | 40 | 62.5% | 62.5% | 62.5% | 0.4867 | 11.1% | 2.7 | 587 |
| C2 | 40 | 37.5% | 37.5% | 37.5% | 0.3873 | 19.7% | 3.8 | 566 |
| C3 | 40 | 62.5% | 62.5% | 62.5% | 0.4547 | 9.8% | 3.1 | 756 |
| C4 | 40 | 62.5% | 62.5% | 62.5% | 0.4333 | 9.2% | 2.9 | 756 |
| C5 | 40 | 52.5% | 52.5% | 52.5% | 0.4405 | 14.1% | 3.3 | 756 |
| C6 | 40 | 65.0% | 65.0% | 65.0% | 0.4158 | 11.1% | 3.2 | 756 |

---

## Stage 3: Feature Contribution Deltas

### Marginal Deltas (each config vs its direct predecessor)

Delta = config_metric − reference_metric. Positive = feature improves the metric.

| Config | vs | Feature | BenchΔ | StrictΔ | EfficiencyΔ | RetryCostΔ | AvgStepsΔ |
|--------|----|---------|----|---------|-------------|------------|-----------|
| C2 | C1 | +planning=react | -25.0% | -25.0% | -0.0994 | +8.6% | +1.1 |
| C3 | C2 | +correction | +25.0% | +25.0% | +0.0674 | -9.9% | -0.7 |
| C4 | C3 | +memory | +0.0% | +0.0% | -0.0213 | -0.5% | -0.2 |
| C5 | C3 | +checklist | -10.0% | -10.0% | -0.0142 | +4.3% | +0.2 |
| C6 | C3 | +verification_gate | +2.5% | +2.5% | -0.0389 | +1.3% | +0.1 |

### Cumulative Deltas (each config vs C1 baseline)

| Config | vs | Feature | BenchΔ | StrictΔ | EfficiencyΔ | RetryCostΔ | AvgStepsΔ |
|--------|----|---------|----|---------|-------------|------------|-----------|
| C2 | C1 | +planning=react | -25.0% | -25.0% | -0.0994 | +8.6% | +1.1 |
| C3 | C1 | +correction | +0.0% | +0.0% | -0.0320 | -1.3% | +0.4 |
| C4 | C1 | +memory | +0.0% | +0.0% | -0.0533 | -1.9% | +0.2 |
| C5 | C1 | +checklist | -10.0% | -10.0% | -0.0462 | +3.0% | +0.6 |
| C6 | C1 | +verification_gate | +2.5% | +2.5% | -0.0709 | -0.1% | +0.5 |

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
