# Rebaseline Playbook 0.6.0

> Date: 2026-04-08
> Scope: completed playbook for the runtime-contract and official SWE-bench Lite subset changes landed in `v0.6.0`

## Goal

`0.6.0` starts a new rebaseline cycle because the eval runtime and benchmark contract changed materially:

- labeled eval runs now use per-run workspaces under `<workspace>/<config_label>/<run_id>/`
- workspace is threaded through the agent, tools, memory lookup/write paths, prompt metadata, and import-error guidance
- resume now has a strict manifest compatibility contract instead of weak checkpoint-only reuse
- a version-pinned official SWE-bench Lite subset is now available for repository-level smoke and compare runs

The fresh artifact set has now been produced on this runtime. The accepted benchmark source of truth is `BASELINE_0_6_0.md`, and `BASELINE_0_5_1.md` remains the archived pre-`0.6.0a` baseline.

## Runtime Contract

All labeled eval manifests must record:

- `run_id`
- `workspace_path`
- `workspace_mode = "per_run_v1"`
- `task_ids`
- preset `agent_config` snapshot + hash
- runtime `experiment_config` snapshot + hash
- combined config snapshot hash
- `llm_profile`, `llm_model`, `llm_transport`
- `benchmark_metadata` + `benchmark_metadata_sha256`

Resume semantics for `0.6.0`:

- skip already completed tasks from checkpoint artifacts
- continue remaining tasks in the same run workspace identity
- rebuild each task workspace from setup files before execution
- do not restore prior workspace file state, conversation history, or loop state

Resume must fail fast for:

- legacy manifests missing `run_id`, `workspace_path`, or `workspace_mode`
- benchmark mismatch
- preset mismatch
- agent/runtime config hash mismatch
- LLM profile mismatch
- benchmark metadata mismatch
- task-set mismatch

For SWE-bench Lite subset runs, `benchmark_metadata` must include:

- `dataset_name`
- `dataset_version`
- `source_mode`
- `source_source`
- `subset`
- `official_manifest_path`
- `official_manifest_sha256`
- `overrides_manifest_path`
- `overrides_manifest_sha256`

## Local Gate

```bash
uv run pytest
uv run python -m coder_agent --help
uv run python -m coder_agent eval --help
```

## Completion Record

The `0.6.0` rebaseline closure completed with:

1. A passing local gate on the current codebase.
2. Fresh Custom targeted compare artifacts: `custom_v060_cmp_C3`, `custom_v060_cmp_C4`, `custom_v060_cmp_C6`.
3. A fresh SWE-bench Lite smoke artifact: `swe_smoke_c3_v060i`.
4. A fresh SWE-bench Lite promoted compare artifact set: `swe_promoted_cmp_v060i_C3`, `swe_promoted_cmp_v060i_C6`.
5. Matching `results/*.json`, `*_run_manifest.json`, and `trajectories/*.jsonl` files for each accepted artifact.
6. Manifests that show the new run/workspace contract fields and SWE-bench benchmark metadata hashes.
7. A new versioned baseline report: `BASELINE_0_6_0.md`.

## Reproduction Commands

Custom targeted compare:

```bash
uv run pytest
uv run python -m coder_agent --help
uv run python -m coder_agent eval --help
uv run python -m coder_agent eval --benchmark custom --compare C3,C4,C6 --config-label custom_v060_cmp
```

SWE-bench Lite smoke and compare:

```bash
uv run python -m coder_agent eval --benchmark swebench --swebench-subset smoke --preset C3 --config-label swe_smoke_c3_v060i
uv run python -m coder_agent eval --benchmark swebench --swebench-subset promoted --compare C3,C6 --config-label swe_promoted_cmp_v060i
```

## Notes

- `0.5.1` artifacts remain valid historical accepted data for the old runtime contract.
- `0.6.0` is now closed and accepted via `BASELINE_0_6_0.md`.
- the post-`v0.6.0d` accepted SWE artifacts are `swe_smoke_c3_v060i`, `swe_promoted_cmp_v060i_C3`, and `swe_promoted_cmp_v060i_C6`
- The current SWE-bench integration uses checked-in generated official Lite metadata plus checked-in local runtime overrides, with real `clone -> checkout -> diff -> test` task flow and optional local mirrors for caching.
- The promoted subset is fixed and spans at least three upstream repositories to reduce single-repo bias.
- These runs are intended for internal compare fidelity, not public leaderboard claims.
