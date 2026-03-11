# Release-Candidate Baseline 0.4.0

> Date: 2026-03-10
> Version: 0.4.0
> Scope: release-candidate benchmark artifacts produced by the current codebase

---

## Overview

This report records the only benchmark artifacts that are allowed to feed the public `0.4.0` baseline.

It replaces the old habit of mixing historical `v8` or `v9` best-case numbers into current-branch docs.

Release-candidate runs completed after passing the local gate:

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

## Release-Candidate Results

### HumanEval

| Artifact | Preset | Success | Notes |
|----------|--------|---------|-------|
| `humaneval_rc_c3_040` | `C3` | `156/164 = 95.1%` | ReAct + correction baseline |
| `humaneval_rc_c6_040` | `C6` | `159/164 = 97.0%` | Strongest HumanEval RC result |

Recommended public HumanEval baseline:

- primary promoted result: `C6` at `97.0%`
- supporting comparison result: `C3` at `95.1%`

### Custom

Standalone RC run:

| Artifact | Preset | Success | Notes |
|----------|--------|---------|-------|
| `custom_rc_c4_040` | `C4` | `17/21 = 81.0%` | Standalone memory-enabled Custom run |

RC comparison run:

| Artifact | Preset | Success | Notes |
|----------|--------|---------|-------|
| `custom_rc_cmp_040_C3` | `C3` | `18/21 = 85.7%` | Compare-run baseline |
| `custom_rc_cmp_040_C4` | `C4` | `20/21 = 95.2%` | Strongest Custom RC result |
| `custom_rc_cmp_040_C6` | `C6` | `19/21 = 90.5%` | Verification-gated compare run |

Interpretation:

- `C4` remains the strongest Custom candidate on the release-candidate compare run.
- the gap between `custom_rc_c4_040` and `custom_rc_cmp_040_C4` shows meaningful run-to-run variance on the expanded Custom suite.
- public `0.4.0` docs should cite the exact artifact name they are using instead of collapsing these runs into one blended number.

---

## Failure Snapshot

### HumanEval RC failures

`humaneval_rc_c3_040`

- `HumanEval_73` (`max_steps`)
- `HumanEval_93` (`model_stop`)
- `HumanEval_108` (`model_stop`)
- `HumanEval_130` (`max_steps`)
- `HumanEval_134` (`model_stop`)
- `HumanEval_145` (`max_steps`)
- `HumanEval_147` (`model_stop`)
- `HumanEval_163` (`model_stop`)

`humaneval_rc_c6_040`

- `HumanEval_73` (`max_steps`)
- `HumanEval_93` (`max_steps`)
- `HumanEval_132` (`max_steps`)
- `HumanEval_134` (`max_steps`)
- `HumanEval_145` (`max_steps`)

### Custom RC failures

`custom_rc_c4_040`

- `custom_hard_003` (`tool_exception`)
- `custom_v8_005` (`retry_exhausted`)
- `custom_v8_006` (`retry_exhausted`)
- `custom_v8_009` (`max_steps`)

`custom_rc_cmp_040_C3`

- `custom_hard_001` (`max_steps`)
- `custom_hard_003` (`retry_exhausted`)
- `custom_v8_005` (`tool_exception`)

`custom_rc_cmp_040_C4`

- `custom_v8_005` (`retry_exhausted`)

`custom_rc_cmp_040_C6`

- `custom_hard_003` (`max_steps`)
- `custom_v8_009` (`max_steps`)

---

## Operational Notes

- both compare runs completed successfully but emitted an `AsyncClient.aclose()` warning after completion because the event loop was already closed
- this warning did not prevent result files, manifests, or trajectory files from being written
- the warning should still be treated as follow-up runtime cleanup work after the `0.4.0` baseline is published

---

## Source-of-Truth Artifacts

Metrics:

- `results/humaneval_rc_c3_040.json`
- `results/humaneval_rc_c6_040.json`
- `results/custom_rc_c4_040.json`
- `results/custom_rc_cmp_040_comparison_report.json`
- `results/custom_rc_cmp_040_C3.json`
- `results/custom_rc_cmp_040_C4.json`
- `results/custom_rc_cmp_040_C6.json`

Audit and resume metadata:

- `results/humaneval_rc_c3_040_run_manifest.json`
- `results/humaneval_rc_c6_040_run_manifest.json`
- `results/custom_rc_c4_040_run_manifest.json`
- `results/custom_rc_cmp_040_comparison_manifest.json`
- `results/custom_rc_cmp_040_C3_run_manifest.json`
- `results/custom_rc_cmp_040_C4_run_manifest.json`
- `results/custom_rc_cmp_040_C6_run_manifest.json`

Trajectory analysis inputs:

- `trajectories/humaneval_rc_c3_040.jsonl`
- `trajectories/humaneval_rc_c6_040.jsonl`
- `trajectories/custom_rc_c4_040.jsonl`
- `trajectories/custom_rc_cmp_040_C3.jsonl`
- `trajectories/custom_rc_cmp_040_C4.jsonl`
- `trajectories/custom_rc_cmp_040_C6.jsonl`
