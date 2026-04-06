# Baseline 0.5.1

> Date: 2026-04-05
> Version: 0.5.1
> Status: accepted
> Suite: Custom 40-task benchmark

---

## Accepted Artifact Set

All required `0.5.1` final artifacts now exist with matching manifest and trajectory files.

Primary shipped lanes:

- `c4_m1_final` -> `results/c4_m1_final.json` -> `36/40 = 90.0%`
- `c6_baseline_final` -> `results/c6_baseline_final.json` -> `36/40 = 90.0%`

Supporting final compare lanes:

- `c4_m3_final` -> `results/c4_m3_final.json` -> `37/40 = 92.5%`
- `c6_ctx1_final` -> `results/c6_ctx1_final.json` -> `36/40 = 90.0%`
- `c6_ctx2_final` -> `results/c6_ctx2_final.json` -> `34/40 = 85.0%`
- `c6_ctx3_final` -> `results/c6_ctx3_final.json` -> `36/40 = 90.0%`
- `c6_ctx_all_final` -> `results/c6_ctx_all_final.json` -> `19/40 = 47.5%`

For every artifact above, the matching sources of truth are:

- `results/<label>.json`
- `results/<label>_run_manifest.json`
- `trajectories/<label>.jsonl`

---

## Metric Table

| Lane | Artifact | Result | Interpretation |
|---|---|---:|---|
| C4 default shipped lane | `c4_m1_final` | `90.0%` | Accepted baseline for the current shipped C4 default (`memory_lookup_mode: recency`) |
| C4 optional similarity lane | `c4_m3_final` | `92.5%` | Stronger than the shipped default; retained as optional supporting lane, not auto-promoted in this baseline |
| C6 default shipped lane | `c6_baseline_final` | `90.0%` | Accepted baseline for the current shipped C6 lane |
| C6 + doom-loop threshold 2 | `c6_ctx1_final` | `90.0%` | No improvement over baseline; no promotion |
| C6 + smart observation compression | `c6_ctx2_final` | `85.0%` | Negative delta; no promotion |
| C6 + semantic compaction | `c6_ctx3_final` | `90.0%` | Stable after bug fix, but no net gain; no promotion |
| C6 + ctx1 + ctx2 + ctx3 | `c6_ctx_all_final` | `47.5%` | Catastrophic regression; reject |

---

## Activation Summary

Key activation counters from the accepted final artifacts:

- `c4_m1_final`
  - `approach_memory_injections=51`
  - `memory_injections=39`
  - `db_records_written=40`
- `c4_m3_final`
  - `approach_memory_injections=8`
  - `memory_injections=39`
  - `db_records_written=40`
- `c6_ctx1_final`
  - `doom_loop_warnings_injected=0`
- `c6_ctx2_final`
  - `observations_compressed=212`
- `c6_ctx3_final`
  - `compaction_events=5`
  - no broad crash pattern, but no measurable win over baseline
- `c6_ctx_all_final`
  - `doom_loop_warnings_injected=1`
  - `observations_compressed=221`
  - `compaction_events=30`
  - `loop_exception=2`

---

## Accepted Interpretation

- The shipped `C6` baseline for `0.5.1` is `c6_baseline_final = 90.0%`.
- The shipped `C4` baseline for `0.5.1` is `c4_m1_final = 90.0%`, because that is the current default config behavior on this branch.
- `c4_m3_final` outperformed the shipped `C4` default at `92.5%`, so similarity retrieval is now the strongest optional memory lane and the clearest future promotion candidate.
- `C_ctx1` is not promoted because the lower doom-loop threshold still did not activate in a useful way.
- `C_ctx2` is not promoted because smart compression reduced pass rate.
- `C_ctx3` is not promoted because the semantic compaction fix restored stability but did not improve benchmark score.
- `C_ctx_all` is explicitly rejected for `0.5.1`.

---

## Historical Context

The archived `0.4.0` baseline remains available for reference only:

- `report/BASELINE_0_4_0.md`
- `report/REBASELINE_PLAYBOOK_0_4_0.md`

Those archived metrics were produced on the older 21-task Custom suite and are not directly comparable to the accepted `0.5.1` 40-task results above.
