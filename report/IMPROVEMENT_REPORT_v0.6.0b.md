# Improvement Report v0.6.0b

> Date: 2026-04-07
> Type: behavior/benchmark change

## What Changed

Implemented the `v0.6.0b` official SWE-bench Lite subset integration:

- `coder_agent/eval/benchmarks/swebench/loader.py`
- `coder_agent/eval/benchmarks/swebench/adapter.py`
- `coder_agent/eval/benchmarks/swebench/task_manifest.json`
- `coder_agent/cli/eval.py`
- `coder_agent/eval/runner.py`
- `coder_agent/eval/eval_checkpoint.py`
- `coder_agent/eval/eval_verification.py`

Supporting tests:

- `tests/test_swebench_benchmark.py`
- `tests/test_cli_eval.py`
- `tests/test_shell_tool.py`

Documentation updates:

- `README.md`
- `report/REBASELINE_PLAYBOOK_0_6_0.md`

## Intended Effect On Agent Behavior

- Eval CLI now supports `--benchmark swebench` with fixed `smoke` and `promoted` subsets drawn from official `princeton-nlp/SWE-bench_Lite` instances.
- Each SWE-bench task runs in its own task workspace under the per-run workspace root.
- The adapter now prepares each task via `git clone` plus explicit `git checkout <base_commit>` before the agent runs, optionally using a local mirror path when provided.
- SWE-bench verification now exports `agent.patch`, runs the task test command, and checks `fail_to_pass` / `pass_to_pass` test lists instead of using exit code alone.
- SWE-bench verification now supports task-scoped upstream regression overlays for selected files during verification. This keeps `agent.patch` limited to model edits while allowing smoke tasks to validate against post-fix regression coverage from repository history.
- Run manifests now record benchmark-specific metadata, including the task manifest hash and `source_mode`.

## Public/Interface Changes

- CLI:
  - `uv run python -m coder_agent eval --benchmark swebench`
  - `--swebench-subset [smoke|promoted]`
- Manifest additions:
  - `benchmark_metadata`
  - `benchmark_metadata_sha256`
- New verification contract:
  - `swebench_patch_and_test`

## Smoke Validation

The implementation is intended to support two local smoke flows:

- single-run smoke subset execution
- promoted compare execution on a fixed task manifest

These runs support direct upstream clone and optional local mirror caching.

Follow-up hardening for the `pylint-dev__pylint-5859` smoke task:

- the smoke `test_command` now uses `-p no:benchmark` instead of `--benchmark-disable`, because the benchmark plugin was still crashing during pytest configuration
- the smoke task now declares `verification_ref = origin/main` and overlays `tests/checkers/unittest_misc.py` during verification so the `fail_to_pass` node exists and matches upstream regression coverage
- the smoke task prompt/expected targets now explicitly include `tests/checkers/unittest_misc.py`

## Scope Clarification

`v0.6.0b` now uses a fixed subset of official `SWE-bench_Lite` tasks with real upstream repo URLs, official instance IDs, and recorded base commits. The subset remains intentionally small for local experimentation, and the project still does not implement the full Docker-based upstream evaluation harness, so benchmark claims should remain limited to internal compare fidelity rather than public leaderboard equivalence.

## Rebaseline Requirement

Yes. A rebaseline is required.

Reason:

- benchmark surface area changed materially
- manifests now carry benchmark-specific metadata
- new promoted compare lanes exist for a fixed official Lite subset spanning multiple upstream repositories

Old `0.5.1` benchmark numbers remain historical accepted artifacts until fresh `0.6.0` artifacts are produced and documented.
