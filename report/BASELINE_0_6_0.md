# Baseline 0.6.0

> Date: 2026-04-08
> Version: 0.6.0
> Status: accepted
> Runtime: per-run eval workspace + strict resume contract

---

## Accepted Artifact Set

All `0.6.0` closure artifacts were regenerated on the new runtime contract with matching `results/*.json`, `*_run_manifest.json`, and `trajectories/*.jsonl` files.

Custom targeted compare:

- `custom_v060_cmp_C3` -> `results/custom_v060_cmp_C3.json` -> `36/40 = 90.0%`
- `custom_v060_cmp_C4` -> `results/custom_v060_cmp_C4.json` -> `35/40 = 87.5%`
- `custom_v060_cmp_C6` -> `results/custom_v060_cmp_C6.json` -> `34/40 = 85.0%`
- `custom_v060_cmp_comparison_report.json` -> targeted compare summary for `C3/C4/C6`

SWE-bench validation:

- `swe_smoke_c3_v060i` -> `results/swe_smoke_c3_v060i.json` -> `1/1 = 100.0%`
- `swe_promoted_cmp_v060i_C3` -> `results/swe_promoted_cmp_v060i_C3.json` -> `0/3 = 0.0%`
- `swe_promoted_cmp_v060i_C6` -> `results/swe_promoted_cmp_v060i_C6.json` -> `0/3 = 0.0%`
- `swe_promoted_cmp_v060i_comparison_report.json` -> promoted compare summary for `C3/C6`

For every accepted artifact above, the matching audit files are:

- `results/<label>.json`
- `results/<label>_run_manifest.json`
- `trajectories/<label>.jsonl`

---

## Local Gate

The required `0.6.0` local gate passed before artifact generation:

- `uv run pytest`
- `uv run python -m coder_agent --help`
- `uv run python -m coder_agent eval --help`

The fresh artifact set above was generated on the `minimax_m27` LLM profile.

---

## Metric Table

| Lane | Artifact | Result | Interpretation |
|---|---|---:|---|
| Custom targeted compare | `custom_v060_cmp_C3` | `90.0%` | Best `0.6.0` Custom lane among the targeted `C3/C4/C6` reruns |
| Custom targeted compare | `custom_v060_cmp_C4` | `87.5%` | Memory lane regressed vs `C3` on the new runtime |
| Custom targeted compare | `custom_v060_cmp_C6` | `85.0%` | Verification-gated lane regressed further vs `C3` on the new runtime |
| SWE-bench smoke | `swe_smoke_c3_v060i` | `100.0%` | Real upstream smoke task now passes on the current harness/runtime stack |
| SWE-bench promoted compare | `swe_promoted_cmp_v060i_C3` | `0.0%` | Repo-repair compare lane now produces cleaner task-level failures, but no promoted task success yet |
| SWE-bench promoted compare | `swe_promoted_cmp_v060i_C6` | `0.0%` | Verification-gated compare lane also remains at `0/3`, with cleaner task-level signals than pre-fix runs |

---

## Runtime Contract Audit

The `0.6.0` manifests now consistently record:

- `run_id`
- `workspace_path`
- `workspace_mode = "per_run_v1"`
- strict config and benchmark metadata hashes

For the accepted SWE-bench artifacts, the manifests also record:

- `dataset_name = "princeton-nlp/SWE-bench_Lite"`
- `source_mode = "official_lite_generated_v1"`
- `official_manifest_sha256 = 3d47e63ec630b9b4968f809351ac871be07dda74ef36d9d33d071f97956cea2d`
- `overrides_manifest_sha256 = 52e3026df7cf48a0f9f6cac5a943a5c435f281f57ed1bb279cdba6f2770198ac`

The `swe_promoted_cmp_v060i_C3` and `swe_promoted_cmp_v060i_C6` manifests use identical official and override hashes, so the compare lane is task-set and contract aligned.

---

## Accepted Interpretation

- `0.6.0` is now the accepted baseline for the current runtime contract.
- The strongest fresh Custom result in the targeted `C3/C4/C6` rerun is `custom_v060_cmp_C3 = 90.0%`.
- On this codebase and provider profile, the `C4` and `C6` lanes underperform `C3` on the 40-task Custom suite, so they are retained as supporting compare lanes rather than promoted winners.
- The SWE-bench Lite smoke and promoted compare lanes are now part of the accepted `0.6.0` audit trail, but they should be interpreted as repository-repair validation artifacts, not public leaderboard claims.
- The accepted post-`v0.6.0d` rerun shows that `pylint-dev__pylint-5859` now passes under the current harness, while the promoted subset remains a task-quality bottleneck rather than a host-environment bottleneck.
- The accepted `0.6.0` evidence set is intentionally narrower than a full `C1–C6` research ablation. It is a release-closure targeted compare plus SWE-bench validation set.

---

## Residual Risks

- SWE-bench task environments are still minimally provisioned. Some trajectories show the agent attempting ad hoc package installation inside upstream repos, which adds noise to repo-repair evaluation.
- The accepted SWE-bench smoke run now passes, but promoted quality on the fixed subset is still `0%`.
- `sympy__sympy-22005` still mixes genuine long-running repo-repair difficulty with intermittent provider-side tool-call protocol `2013` errors.
- `sphinx-doc__sphinx-8273` no longer shows host-environment contamination, but still mixes extension/dependency setup noise with agent failure.
- Some failing Custom and SWE-bench trajectories still show the agent drifting into test edits or test-expectation fixes; this is a model behavior issue, not a manifest/harness issue.
- The `results_mbpp/*` deletions currently visible in the worktree are unrelated historical cleanup noise and are not part of this baseline.

---

## Historical Context

- [BASELINE_0_5_1.md](./BASELINE_0_5_1.md) remains the archived accepted baseline for the pre-`0.6.0a` runtime.
- [REBASELINE_PLAYBOOK_0_6_0.md](./REBASELINE_PLAYBOOK_0_6_0.md) is the reproduction contract for this baseline closure.
