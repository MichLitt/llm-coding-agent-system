# Ablation Report v0.4.3

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
| C1 | 40 | 12.5% | 12.5% | 12.5% | 0.7000 | 1.2% | 1.9 | 533 |
| C2 | 40 | 7.5% | 7.5% | 7.5% | 0.5000 | 0.0% | 2.0 | 512 |
| C3 | 40 | 10.0% | 10.0% | 10.0% | 0.6250 | 0.0% | 2.0 | 701 |
| C4 | 40 | 7.5% | 7.5% | 7.5% | 0.5000 | 0.0% | 2.0 | 701 |
| C5 | 40 | 10.0% | 10.0% | 10.0% | 0.6250 | 0.0% | 2.0 | 701 |
| C6 | 40 | 7.5% | 7.5% | 7.5% | 0.5000 | 0.0% | 2.0 | 701 |

---

## Stage 3: Feature Contribution Deltas

### Marginal Deltas (each config vs its direct predecessor)

Delta = config_metric − reference_metric. Positive = feature improves the metric.

| Config | vs | Feature | BenchΔ | StrictΔ | EfficiencyΔ | RetryCostΔ | AvgStepsΔ |
|--------|----|---------|----|---------|-------------|------------|-----------|
| C2 | C1 | +planning=react | -5.0% | -5.0% | -0.2000 | -1.2% | +0.1 |
| C3 | C2 | +correction | +2.5% | +2.5% | +0.1250 | +0.0% | -0.0 |
| C4 | C3 | +memory | -2.5% | -2.5% | -0.1250 | +0.0% | +0.0 |
| C5 | C3 | +checklist | +0.0% | +0.0% | +0.0000 | +0.0% | +0.0 |
| C6 | C3 | +verification_gate | -2.5% | -2.5% | -0.1250 | +0.0% | +0.0 |

### Cumulative Deltas (each config vs C1 baseline)

| Config | vs | Feature | BenchΔ | StrictΔ | EfficiencyΔ | RetryCostΔ | AvgStepsΔ |
|--------|----|---------|----|---------|-------------|------------|-----------|
| C2 | C1 | +planning=react | -5.0% | -5.0% | -0.2000 | -1.2% | +0.1 |
| C3 | C1 | +correction | -2.5% | -2.5% | -0.0750 | -1.2% | +0.0 |
| C4 | C1 | +memory | -5.0% | -5.0% | -0.2000 | -1.2% | +0.1 |
| C5 | C1 | +checklist | -2.5% | -2.5% | -0.0750 | -1.2% | +0.0 |
| C6 | C1 | +verification_gate | -5.0% | -5.0% | -0.2000 | -1.2% | +0.1 |

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
