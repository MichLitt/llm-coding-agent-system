# Improvement Report v0.7.1

> Date: 2026-04-08
> Scope: remove remaining system noise from the 8-task SWE promoted lane and close the `0.7.1` cycle

## What Changed

`0.7.1` focuses on three concrete sources of distortion seen in the `0.7.0` promoted rerun:

1. shell exit masking
2. verification overlay conflict
3. under-specified task-local setup and over-broad failure attribution

Implemented changes:

- `coder_agent/tools/shell_tool.py`
  - wrap piped shell commands with `bash -o pipefail -lc ...`
  - preserve the existing output contract while making upstream `pytest` failures visible
- `coder_agent/core/agent_prompt.py`
  - explicitly forbid using `| tail`, `| head`, or `| grep` to decide whether tests passed
- `coder_agent/core/agent_errors.py`
  - verification guidance now repeats the same restriction in the high-pressure retry path
- `coder_agent/eval/benchmarks/swebench/adapter.py`
  - verification overlay now temporarily clears authorized conflicting regression files before applying official `test_patch`
  - overlay teardown restores the agent workspace state afterward
- `coder_agent/eval/eval_verification.py`
  - pass authorized overlay-replaceable paths into verification overlay handling
- `coder_agent/eval/benchmarks/swebench/local_overrides.json`
  - add `roman` to `sphinx-doc__sphinx-8273` task-local setup
- `coder_agent/eval/analysis_taxonomy.py`
  - add `verification_overlay_conflict`
  - add `shell_exit_masking`
  - then tighten `shell_exit_masking` so it only remains primary when the trajectory never moves past the masked pipeline success
  - keep `infra_setup_failure` on a strict provisioning-only definition

Tests added or updated:

- `tests/test_shell_tool.py`
- `tests/test_swebench_benchmark.py`
- `tests/test_analysis.py`

## Intended Effect

- `pytest-dev__pytest-7373` should no longer be dominated by shell pipeline false positives once the agent performs later direct reruns
- `pallets__flask-4992` should no longer fail because the official verification overlay cannot apply on top of an agent-created regression file
- `sphinx-doc__sphinx-8273` should no longer fail immediately on the earlier missing `roman` provisioning gap
- layered analysis reports should move away from the earlier over-broad “infra/setup failure” interpretation

## Rerun Results

Formal promoted compare:

- `swe_promoted_cmp_v071r1_C3` -> `2/8 = 25.0%`
- `swe_promoted_cmp_v071r1_C6` -> `2/8 = 25.0%`

Supporting lane:

- `swe_promoted_support_v071r1_C4` -> `2/8 = 25.0%`

Passing tasks across all three lanes:

- `pylint-dev__pylint-5859`
- `pylint-dev__pylint-7993`

This means `0.7.1` does not raise promoted pass rate. The closure value is cleaner attribution, not benchmark uplift.

## Attribution Outcome

The accepted post-tightening analysis is:

- `pytest-dev__pytest-7373`
  - probe: `genuine_implementation_miss`
  - formal `C3/C6`: no longer classified as `shell_exit_masking`
  - remaining issue is verification convergence, not shell semantics
- `pallets__flask-4992`
  - no longer classified as `verification_overlay_conflict`
  - remaining failure is model-side patch direction / public interface mismatch
- `sphinx-doc__sphinx-8273`
  - no longer blocked by the earlier missing `roman` dependency
  - still not a clean implementation task; it mixes task-level edits with extension compatibility noise

Formal lane layered counts after tightening:

- `C3`: `wrong_file_edit=2`, `test_drift=2`, `genuine_implementation_miss=2`, `shell_exit_masking=0`
- `C6`: `wrong_file_edit=2`, `test_drift=1`, `genuine_implementation_miss=3`, `shell_exit_masking=0`
- `C4`: `wrong_file_edit=1`, `test_drift=1`, `genuine_implementation_miss=3`, `shell_exit_masking=1`

## Rebaseline Decision

Rebaseline is required and completed for the `0.7.1` cycle.

Reason:

- `run_command` semantics changed in a benchmark-relevant way
- SWE verification overlay behavior changed in a benchmark-relevant way
- layered analysis semantics changed in a benchmark-relevant way

Fresh accepted artifacts are:

- `swe_promoted_cmp_v071r1_C3`
- `swe_promoted_cmp_v071r1_C6`
- `swe_promoted_support_v071r1_C4`
- `swe_probe_pytest7373_v071c`
- `swe_probe_flask4992_v071`
- `swe_probe_sphinx8273_v071`

## Promotion Decision

- `C4` is not promoted
- formal compare lanes remain `C3` and `C6`
- `0.7.1` closes as a system-noise reduction release, not a higher-score release

## SWE Scope Decision

The formal SWE promoted lane remains:

- `8` tasks
- `5` repos

The formal lane is not expanded in `0.7.1`.

Reason:

- this cycle was blocked by attribution quality, not by insufficient sample count
- expanding the lane before stabilizing attribution would have increased noise faster than signal

If `0.7.2` is opened, expansion to `12-16` tasks should only happen after:

- one more clean `C3/C6` rerun
- stable layered attribution on the current `8`-task lane
- no major residual harness-side distortions
