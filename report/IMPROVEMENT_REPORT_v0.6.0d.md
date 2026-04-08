# Improvement Report v0.6.0d

> Date: 2026-04-08
> Scope: SWE-bench promoted task-local provisioning hardening

## What Changed

This update tightens SWE-bench promoted task isolation in two places:

- `coder_agent/eval/benchmarks/swebench/local_overrides.json`
  - added task-local `.swebench-venv` setup for the promoted tasks
  - pinned promoted tasks to task-appropriate Python versions, with `pytest-dev__pytest-7220` using `3.9`
  - added explicit `pytest` installation for the promoted tasks so `python -m pytest` resolves inside the task-local venv instead of failing or falling back to the host environment
  - added explicit `xmlschema` and `hypothesis` installation for `pytest-dev__pytest-7220`, which are imported during test collection
  - pinned `sphinx-doc__sphinx-8273` to `setuptools<81` so `pkg_resources` remains available during test startup
  - pinned `jinja2<3.1` for `sphinx-doc__sphinx-8273`, whose code imports the removed `environmentfilter` API
  - narrowed the promoted `pytest` and `sphinx` verification commands to their task-relevant test files instead of running unrelated full-suite collection
- `coder_agent/core/workspace_env.py`
  - when a task-local venv is active, remove `PYTHONPATH`, `PYTHONHOME`, and `__PYVENV_LAUNCHER__`
  - set `PYTHONNOUSERSITE=1`
  - keep `VIRTUAL_ENV`, `PATH`, and `UV_CACHE_DIR` bound to the workspace-local environment

Tests updated:

- `tests/test_swebench_benchmark.py`
- `tests/test_shell_tool.py`

## Intended Effect

- promoted SWE-bench tasks should now execute under a task-local Python environment instead of leaking into the host project `.venv`
- failures in promoted runs should move from “wrong interpreter / wrong site-packages” toward task-local dependency or genuine agent/task failures
- shell execution in task-local venv contexts should be more hermetic and less sensitive to host Python environment variables

## Validation

Focused regression passed:

- `uv run pytest tests/test_swebench_benchmark.py`
- `uv run pytest tests/test_shell_tool.py`

Observed runtime validation:

- a fresh `swe_promoted_probe` run created `workspace/.../.swebench-venv/bin/python`
- after adding `pytest` to promoted setup, the probe advanced beyond the earlier `No module named pytest` failure, confirming the promoted lane is now using task-local provisioning rather than the host `.venv`
- after binding `python3` to the workspace interpreter and narrowing promoted verification to task-relevant test files, fresh formal reruns produced:
  - `swe_smoke_c3_v060i` -> `1/1`
  - `swe_promoted_cmp_v060i_C3` -> `0/3`
  - `swe_promoted_cmp_v060i_C6` -> `0/3`
- these reruns no longer show the earlier host `.venv` contamination pattern; promoted failures are now primarily task-level dependency, extension, timeout, or agent-quality failures

## Rebaseline Impact

Rebaseline is required before citing benchmark numbers again.

Reason:

- this change alters effective tool/runtime behavior for benchmark tasks by changing how Python commands resolve and which environment variables are exposed inside task-local runs
- under the repository rules, tool behavior changes invalidate existing accepted benchmark numbers for current-cycle citations

Fresh post-hardening artifacts have now been generated (`swe_smoke_c3_v060i`, `swe_promoted_cmp_v060i_C3`, `swe_promoted_cmp_v060i_C6`), so `BASELINE_0_6_0.md` should cite those post-`v0.6.0d` artifacts rather than the earlier pre-hardening SWE numbers.
