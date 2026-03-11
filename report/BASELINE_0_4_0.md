# Baseline 0.4.0

> Date: 2026-03-11
> Version: 0.4.0
> Scope: final accepted benchmark artifacts produced by the current codebase

---

## Overview

This report records the final accepted `0.4.0` benchmark artifacts.

It replaces both:

- historical `v8`/`v9` benchmark promotion as the default public story
- the first `0.4.0` release-candidate rerun as the primary source of truth

Final acceptance happened after passing the local gate:

- `uv run pytest`
- `uv run python -m coder_agent --help`
- default REPL startup with `/exit`

---

## Runtime Contract

The supported runtime path for `0.4.0` is an OpenAI-compatible backend:

- required: `LLM_API_KEY`
- optional: `LLM_BASE_URL`
- optional: `CODER_MODEL`

`model.provider` remains compatibility-only metadata in this milestone.

---

## Promoted Preset Scope

Only these presets are in scope for the `0.4.0` public benchmark story:

- `C3`
- `C4`
- `C6`

`C5` remains experimental and does not enter promoted `0.4.0` tables.

---

## Final Results

### HumanEval

| Artifact | Preset | Success | Notes |
|----------|--------|---------|-------|
| `humaneval_040_final_c3` | `C3` | `157/164 = 95.7%` | supporting ReAct + correction reference |
| `humaneval_040_final_c6` | `C6` | `161/164 = 98.2%` | strongest final HumanEval result |

Recommended public HumanEval baseline:

- primary promoted result: `humaneval_040_final_c6` at `98.2%`
- supporting comparison result: `humaneval_040_final_c3` at `95.7%`

### Custom

Standalone final run:

| Artifact | Preset | Success | Notes |
|----------|--------|---------|-------|
| `custom_040_final_c4` | `C4` | `19/21 = 90.5%` | standalone memory-enabled Custom run |

Final comparison run:

| Artifact | Preset | Success | Notes |
|----------|--------|---------|-------|
| `custom_040_final_cmp_C3` | `C3` | `20/21 = 95.2%` | clean supporting `C3` compare artifact retained from the first compare run |
| `custom_040_final_cmp_retry_C4` | `C4` | `20/21 = 95.2%` | retry-recovered memory-enabled compare artifact |
| `custom_040_final_cmp_retry_C6` | `C6` | `21/21 = 100.0%` | promoted final Custom compare baseline |

Recommended public Custom baseline:

- primary promoted result: `custom_040_final_cmp_retry_C6` at `100.0%`
- supporting `C3` compare reference: `custom_040_final_cmp_C3` at `95.2%`
- supporting memory-enabled compare reference: `custom_040_final_cmp_retry_C4` at `95.2%`
- supporting standalone memory reference: `custom_040_final_c4` at `90.5%`

Interpretation:

- `C6` produced the strongest final accepted Custom compare artifact after retrying the polluted compare lanes
- `C3` remains a useful clean supporting compare reference from the original final compare run
- `C4` now has both a standalone memory reference and a retry-recovered compare reference
- `custom_040_final_cmp_C4` and `custom_040_final_cmp_C6` are superseded polluted artifacts retained only for audit/history

---

## Failure Snapshot

### HumanEval final failures

`humaneval_040_final_c3`

- `HumanEval_54` (`model_stop`)
- `HumanEval_93` (`model_stop`)
- `HumanEval_108` (`model_stop`)
- `HumanEval_110` (`model_stop`)
- `HumanEval_130` (`model_stop`)
- `HumanEval_145` (`model_stop`)
- `HumanEval_147` (`model_stop`)

`humaneval_040_final_c6`

- `HumanEval_108` (`verification_failed`)
- `HumanEval_130` (`max_steps`)
- `HumanEval_145` (`max_steps`)

### Custom final failures

`custom_040_final_c4`

- `custom_hard_003` (`max_steps`)
- `custom_v8_005` (`max_steps`)

`custom_040_final_cmp_C3`

- `custom_hard_003` (`retry_exhausted`)

`custom_040_final_cmp_retry_C4`

- `custom_hard_003` (`retry_exhausted`)

`custom_040_final_cmp_retry_C6`

- none

### Superseded polluted compare attempt

`custom_040_final_cmp_C4`

- `custom_v8_002` (`loop_exception`)
- `custom_v8_003` (`loop_exception`)
- `custom_v8_004` (`loop_exception`)
- `custom_v8_005` (`loop_exception`)
- `custom_v8_006` (`loop_exception`)
- `custom_v8_007` (`loop_exception`)
- `custom_v8_008` (`loop_exception`)
- `custom_v8_009` (`loop_exception`)
- `custom_v8_010` (`loop_exception`)

`custom_040_final_cmp_C6`

- `custom_easy_001` (`loop_exception`)
- `custom_easy_002` (`loop_exception`)
- `custom_medium_001` (`loop_exception`)
- `custom_medium_003` (`loop_exception`)
- `custom_medium_004` (`loop_exception`)
- `custom_medium_005` (`loop_exception`)
- `custom_hard_001` (`loop_exception`)
- `custom_hard_003` (`retry_exhausted`)
- `custom_v8_007` (`max_steps`)

---

## Operational Notes

- the post-RC `AsyncClient.aclose()` / `Event loop is closed` warning was not observed in this final accepted cycle
- the first final compare run exposed a different operational problem: bursts of provider/API connection failure during `llm.chat`
- this instability materially affected the original `custom_040_final_cmp_C4` and `custom_040_final_cmp_C6` lanes
- the retry compare run recovered accepted final `C4` and `C6` artifacts as `custom_040_final_cmp_retry_C4` and `custom_040_final_cmp_retry_C6`
- for `0.4.0`, public docs should therefore cite the exact final artifact name being discussed and treat the polluted first-pass `C4`/`C6` compare results as superseded audit data

---

## Source-of-Truth Artifacts

Metrics:

- `results/humaneval_040_final_c3.json`
- `results/humaneval_040_final_c6.json`
- `results/custom_040_final_c4.json`
- `results/custom_040_final_cmp_C3.json`
- `results/custom_040_final_cmp_retry_comparison_report.json`
- `results/custom_040_final_cmp_retry_C4.json`
- `results/custom_040_final_cmp_retry_C6.json`

Audit and resume metadata:

- `results/humaneval_040_final_c3_run_manifest.json`
- `results/humaneval_040_final_c6_run_manifest.json`
- `results/custom_040_final_c4_run_manifest.json`
- `results/custom_040_final_cmp_C3_run_manifest.json`
- `results/custom_040_final_cmp_retry_comparison_manifest.json`
- `results/custom_040_final_cmp_retry_C4_run_manifest.json`
- `results/custom_040_final_cmp_retry_C6_run_manifest.json`

Trajectory analysis inputs:

- `trajectories/humaneval_040_final_c3.jsonl`
- `trajectories/humaneval_040_final_c6.jsonl`
- `trajectories/custom_040_final_c4.jsonl`
- `trajectories/custom_040_final_cmp_C3.jsonl`
- `trajectories/custom_040_final_cmp_retry_C4.jsonl`
- `trajectories/custom_040_final_cmp_retry_C6.jsonl`

Superseded polluted artifacts retained for audit/history:

- `results/custom_040_final_cmp_comparison_report.json`
- `results/custom_040_final_cmp_C4.json`
- `results/custom_040_final_cmp_C6.json`
- `results/custom_040_final_cmp_comparison_manifest.json`
- `results/custom_040_final_cmp_C4_run_manifest.json`
- `results/custom_040_final_cmp_C6_run_manifest.json`
- `trajectories/custom_040_final_cmp_C4.jsonl`
- `trajectories/custom_040_final_cmp_C6.jsonl`

Archive/reference only:

- `report/BASELINE_0_4_0_RC.md`
