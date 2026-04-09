# Baseline 0.7.1

> Date: 2026-04-08
> Version: 0.7.1
> Status: accepted
> Focus: SWE promoted noise-reduction and attribution cleanup

---

## Accepted Artifact Set

Formal promoted compare:

- `swe_promoted_cmp_v071r1_C3` -> `results/swe_promoted_cmp_v071r1_C3.json` -> `2/8 = 25.0%`
- `swe_promoted_cmp_v071r1_C6` -> `results/swe_promoted_cmp_v071r1_C6.json` -> `2/8 = 25.0%`
- `swe_promoted_cmp_v071r1_comparison_report.json` -> formal `C3/C6` compare summary

Supporting lane:

- `swe_promoted_support_v071r1_C4` -> `results/swe_promoted_support_v071r1_C4.json` -> `2/8 = 25.0%`

Probe artifacts used for attribution audit:

- `swe_probe_pytest7373_v071c`
- `swe_probe_flask4992_v071`
- `swe_probe_sphinx8273_v071`

For all accepted artifacts above, the matching audit files are present:

- `results/<label>.json`
- `results/<label>_run_manifest.json`
- `results/<label>_analysis_report.json`
- `trajectories/<label>.jsonl`

---

## Accepted Interpretation

- `0.7.1` does not improve promoted pass rate over `0.7.0`; formal `C3` and `C6` remain tied at `2/8`.
- `C4` also lands at `2/8`, so it remains a supporting lane and is not promoted.
- The accepted value of `0.7.1` is not higher score; it is cleaner failure composition.
- Two previously noisy system-side failures were materially reduced:
  - `pallets__flask-4992` no longer fails because verification overlay cannot apply the official `test_patch`
  - `pytest-dev__pytest-7373` no longer needs to be interpreted primarily as a shell pipe false-positive issue in the accepted probe and formal `C3/C6` analyses
- `sphinx-doc__sphinx-8273` no longer fails immediately on the earlier missing-`roman` setup gap, although it still mixes task-level test drift and extension/environment incompatibility.

The only promoted tasks that pass across all three rerun lanes are:

- `pylint-dev__pylint-5859`
- `pylint-dev__pylint-7993`

---

## Layered Analysis Snapshot

Fresh post-tightening `analysis_report.json` outputs show:

- `swe_promoted_cmp_v071r1_C3`
  - `wrong_file_edit = 2`
  - `test_drift = 2`
  - `genuine_implementation_miss = 2`
  - `shell_exit_masking = 0`
- `swe_promoted_cmp_v071r1_C6`
  - `wrong_file_edit = 2`
  - `test_drift = 1`
  - `genuine_implementation_miss = 3`
  - `shell_exit_masking = 0`
- `swe_promoted_support_v071r1_C4`
  - `wrong_file_edit = 1`
  - `test_drift = 1`
  - `genuine_implementation_miss = 3`
  - `shell_exit_masking = 1`

This is the accepted `0.7.1` interpretation boundary:

- formal `C3/C6` numbers are now clean enough to compare without the earlier overlay-conflict and broad shell-masking distortion
- supporting `C4` still shows one shell-masking trajectory, which is one reason it is not promoted

---

## SWE Scope Decision

- The formal promoted SWE lane remains fixed at `8` tasks across `5` repos.
- `0.7.1` explicitly does not expand the formal promoted set to `12-16` tasks.
- Expansion is deferred to `0.7.2+` because attribution quality was the gating issue in this cycle, not data volume.

---

## Local Gate

The required local validation passed on the accepted `0.7.1` code line:

- `uv run pytest tests/test_analysis.py`
- `uv run pytest`
- `uv run python -m coder_agent analyze swe_promoted_cmp_v071r1_C3`
- `uv run python -m coder_agent analyze swe_promoted_cmp_v071r1_C6`
- `uv run python -m coder_agent analyze swe_promoted_support_v071r1_C4`
- `uv run python -m coder_agent analyze swe_probe_pytest7373_v071c`
- `uv run python -m coder_agent analyze swe_probe_flask4992_v071`
- `uv run python -m coder_agent analyze swe_probe_sphinx8273_v071`

The reruns above used the `glm_5` profile sourced from the local `.env`.

---

## Residual Risks

- `sphinx-doc__sphinx-8273` still mixes repo/task-level incompatibility with agent behavior; it is cleaner than `0.7.0` but not yet a pure implementation task.
- `pytest-dev__pytest-7373` still ends in external verification failure in `C4`, so shell semantics are no longer the dominant explanation, but verification strategy remains imperfect.
- `pallets__flask-4992` no longer suffers overlay apply conflict, but the model still drifts into a partially mismatched public interface fix (`mode="b"` vs internal open mode handling).
- The promoted lane remains small, so `0.7.1` should be interpreted as a release-closure diagnostic baseline, not a broad SWE scaling claim.

---

## Historical Context

- [BASELINE_0_6_0.md](./BASELINE_0_6_0.md) remains the accepted pre-`0.7.x` baseline.
- [REBASELINE_PLAYBOOK_0_7_1.md](./REBASELINE_PLAYBOOK_0_7_1.md) is the reproduction contract for this closure.
