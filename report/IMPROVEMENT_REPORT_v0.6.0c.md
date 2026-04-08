# Improvement Report v0.6.0c

> Date: 2026-04-07
> Type: benchmark/runtime contract change

## What Changed

Reworked the SWE-bench task contract to use a checked-in generated official manifest plus checked-in local overrides:

- added `coder_agent/eval/benchmarks/swebench/official_tasks.source.json`
- added `coder_agent/eval/benchmarks/swebench/manifest_export.py`
- added `scripts/export_swebench_manifest.py`
- added `coder_agent/eval/benchmarks/swebench/official_manifest.generated.json`
- added `coder_agent/eval/benchmarks/swebench/local_overrides.json`
- updated `coder_agent/eval/benchmarks/swebench/loader.py`
- updated `coder_agent/eval/benchmarks/swebench/adapter.py`
- updated `coder_agent/eval/eval_verification.py`
- updated `coder_agent/eval/runner.py`

Supporting tests:

- `tests/test_swebench_benchmark.py`
- `tests/test_cli_eval.py`
- `tests/test_eval_runner.py`
- `tests/test_shell_tool.py`

Documentation updates:

- `README.md`
- `report/REBASELINE_PLAYBOOK_0_6_0.md`
- `report/IMPROVEMENT_PLAN_v0.6.0.md`

## Intended Effect On Agent Behavior

- SWE-bench task loading now treats checked-in generated official metadata as the source of truth for task identity, commits, issue text, fail-to-pass, pass-to-pass, and test patch data.
- Local runtime differences such as subset selection, Python version, setup commands, and test command shape are isolated into a separate override layer.
- Verification no longer depends on hand-written `verification_ref` / `verification_files`; it overlays `test_patch` directly during verification while keeping `agent.patch` limited to the model's changes.
- Run manifests now audit both the official manifest hash and the local overrides hash.

## Public/Interface Changes

- New command:
  - `uv run python scripts/export_swebench_manifest.py`
- SWE-bench checked-in data files are now:
  - `official_manifest.generated.json`
  - `local_overrides.json`
- `benchmark_metadata` for SWE-bench runs now records:
  - `official_manifest_path`
  - `official_manifest_sha256`
  - `overrides_manifest_path`
  - `overrides_manifest_sha256`

## Rebaseline Requirement

Yes. A rebaseline is required.

Reason:

- SWE-bench benchmark inputs changed materially
- SWE-bench verification contract changed from hand-written overlay metadata to official `test_patch` overlay semantics
- run manifest audit fields changed for SWE-bench artifact comparison
