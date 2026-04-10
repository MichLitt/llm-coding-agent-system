# LLM Coding Agent System

LLM Coding Agent System is a ReAct-style coding agent for local software tasks. It combines tool use, trajectory logging, benchmark runners, and analysis utilities in one repo so the agent can be developed and evaluated with the same codebase.

License: [MIT](./LICENSE).

## Core Capabilities

- Interactive multi-turn CLI session via `coder-agent` / `python -m coder_agent`
- ReAct-style agent loop with tool calling and self-correction
- Task-aware verification gate for benchmark termination
- Custom benchmark runner for multi-step coding tasks
- Official SWE-bench Lite subset runner for repository repair smoke and compare lanes
- HumanEval runner for function-level benchmark evaluation
- Trajectory analysis, layered failure reports, and experiment comparison

## Current Status

Current code version: `0.7.1`

Current accepted baseline cycle: `0.7.1` via `report/BASELINE_0_7_1.md`

The accepted `0.7.1` cycle closes the `0.7.0` workstream on top of the earlier `0.6.0` runtime-contract baseline. `0.7.0` introduced patch-style editing (`patch_file`), verification-specific recovery guardrails, task-scoped ad hoc install budgets, and layered analysis reports written to `results/<experiment_id>_analysis_report.json`.

The accepted `0.7.1` closure then tightened benchmark-facing runtime semantics and SWE attribution quality: piped shell commands now surface upstream failures via `pipefail`, SWE verification overlay handling avoids agent-created regression-file conflicts, `sphinx-doc__sphinx-8273` provisioning is tighter, and layered taxonomy is stricter about `shell_exit_masking`. The accepted value of `0.7.1` is cleaner failure composition on the fixed SWE promoted lane, not a higher pass rate.

Key points:

- The supported runtime path is an OpenAI-compatible backend configured with `LLM_API_KEY` and optional `LLM_BASE_URL`.
- `model.provider` remains in config for compatibility and is informational only at runtime.
- The active day-to-day presets are `default`, `C3`, `C4`, and `C6`.
- The current accepted closure docs are `BASELINE_0_7_1.md`, `REBASELINE_PLAYBOOK_0_7_1.md`, and `IMPROVEMENT_REPORT_v0.7.1.md`.
- The accepted `0.7.1` SWE promoted artifacts are `swe_promoted_cmp_v071r1_C3`, `swe_promoted_cmp_v071r1_C6`, and supporting `swe_promoted_support_v071r1_C4`.
- The accepted `0.6.0` Custom targeted compare artifacts remain `custom_v060_cmp_C3`, `custom_v060_cmp_C4`, and `custom_v060_cmp_C6`.
- `C5` remains available for checklist experiments, but it is explicitly non-promoted.
- Eval runs with a `config_label` now allocate a unique run workspace under `<workspace>/<config_label>/<run_id>/`.
- `--resume` now means "skip completed tasks and continue remaining tasks"; it does not restore prior workspace state, conversation history, or loop state.
- `--benchmark swebench` now runs a version-pinned official SWE-bench Lite subset with per-task workspaces under `<run_workspace>/<task_id>/`.
- The checked-in SWE smoke subset now covers `3` fixed tasks; the promoted compare subset now covers `8` fixed tasks across `5` upstream repos and remains hash-audited.
- The SWE-bench task source of truth is now `official_manifest.generated.json` plus `local_overrides.json`, not a hand-written single task manifest.
- Promoted SWE-bench tasks now use explicit per-task test-edit authorization where regression-file edits are intentionally allowed, instead of relying on broad implicit fallback.
- `analyze` now writes a machine-readable layered failure report alongside the console summary.
- Formal SWE promoted compare lanes remain `C3` and `C6`; `C4` is retained as a supporting lane and is not promoted in `0.7.1`.

Recommended reading:

- [BASELINE_0_7_1.md](./report/BASELINE_0_7_1.md)
- [REBASELINE_PLAYBOOK_0_7_1.md](./report/REBASELINE_PLAYBOOK_0_7_1.md)
- [IMPROVEMENT_REPORT_v0.7.1.md](./report/IMPROVEMENT_REPORT_v0.7.1.md)
- [BASELINE_0_6_0.md](./report/BASELINE_0_6_0.md)
- [IMPROVEMENT_REPORT_v0.6.0d.md](./report/IMPROVEMENT_REPORT_v0.6.0d.md)
- [BASELINE_0_5_1.md](./report/BASELINE_0_5_1.md)
- [REBASELINE_PLAYBOOK_0_6_0.md](./report/REBASELINE_PLAYBOOK_0_6_0.md)
- [IMPROVEMENT_REPORT_v0.6.0c.md](./report/IMPROVEMENT_REPORT_v0.6.0c.md)
- [IMPROVEMENT_REPORT_v0.6.0b.md](./report/IMPROVEMENT_REPORT_v0.6.0b.md)
- [IMPROVEMENT_REPORT_v0.6.0a.md](./report/IMPROVEMENT_REPORT_v0.6.0a.md)
- [IMPROVEMENT_REPORT_v0.5.1.md](./report/IMPROVEMENT_REPORT_v0.5.1.md)
- [BASELINE_0_4_0.md](./report/BASELINE_0_4_0.md) - archived historical baseline on the 21-task suite
- [REBASELINE_PLAYBOOK_0_4_0.md](./report/REBASELINE_PLAYBOOK_0_4_0.md) - archived historical playbook
- [IMPROVEMENT_SUMMARY.md](./report/IMPROVEMENT_SUMMARY.md) - archived `0.3.0` summary
- [BASELINE_0_4_0_RC.md](./report/BASELINE_0_4_0_RC.md) - archived RC baseline

### Preset Guidance

| Preset | Primary use | 0.7.1 cycle status |
|--------|-------------|--------------|
| `default` | Config-driven interactive use | Active |
| `C3` | ReAct + correction baseline | Formal SWE promoted compare lane |
| `C4` | Multi-step tasks with memory | Supporting SWE lane only; not promoted |
| `C5` | Checklist/decomposer experiments | Experimental |
| `C6` | Verification-gated ReAct baseline | Formal SWE promoted compare lane |

## Quick Start

### 1. Install

```bash
uv sync
cp .env.example .env
```

Set the API key for your chosen profile in `.env` (see [LLM Profile Configuration](#llm-profile-configuration) below).

### 2. Start an interactive session

```bash
uv run python -m coder_agent
```

You can also use:

```bash
uv run python -m coder_agent chat
```

Available in-session commands:

```text
/help
/status
/reset
/clear
/exit
```

### 3. Run the agent on a single task

```bash
uv run python -m coder_agent run "Create a Flask API with user auth"
```

Single-task runs now emit a persistent `run_id`. You can resume a prior run from the latest checkpoint:

```bash
uv run python -m coder_agent run --resume <run_id>
```

Inspect recent runs and a specific checkpoint before resuming:

```bash
uv run python -m coder_agent runs list --limit 20
uv run python -m coder_agent runs show <run_id>
```

Switch provider with `--llm-profile`:

```bash
uv run python -m coder_agent run "Create a Flask API" --llm-profile glm_5
```

Start the local runtime API service:

```bash
uv run python -m coder_agent serve --host 127.0.0.1 --port 8000
```

### 4. Run one benchmark task

```bash
uv run python -m coder_agent eval --benchmark custom --preset C4 --limit 1 --config-label demo
```

Use a specific provider profile for an eval run:

```bash
uv run python -m coder_agent eval --benchmark custom --preset C4 --config-label demo_glm5 \
  --llm-profile glm_5
```

Runtime experiment overrides can be passed as JSON and are captured in the run manifest:

```bash
uv run python -m coder_agent eval --benchmark custom --preset C4 --config-label demo \
  --experiment-config '{"memory_lookup_mode":"similarity","keep_recent_turns":4}'
```

Labeled eval runs now write run-scoped metadata into the manifest, including `run_id`, `workspace_path`, `workspace_mode`, and the requested `task_ids`. Resume only works when the benchmark, preset/config snapshots, LLM profile, and task set still match the original run.

SWE-bench Lite smoke example:

```bash
uv run python -m coder_agent eval --benchmark swebench --swebench-subset smoke \
  --preset C3 --config-label swe_smoke_c3_v060i
```

SWE-bench Lite compare example:

```bash
uv run python -m coder_agent eval --benchmark swebench --swebench-subset promoted \
  --compare C3,C6 --config-label swe_promoted_cmp_v060i
```

Regenerate the checked-in SWE-bench official manifest from the source snapshot:

```bash
uv run python scripts/export_swebench_manifest.py
```

### 5. Analyze an experiment

```bash
uv run python -m coder_agent analyze demo
```

This writes `results/demo_analysis_report.json` in addition to the console summary.

## LLM Profile Configuration

As of v0.5.2, provider configuration is managed through named profiles in `config.yaml`. This replaces the previous single-backend `model:` block.

### Defining profiles

`config.yaml` ships with three profiles:

| Profile | Transport | Model |
|---|---|---|
| `minimax_m27` | anthropic | MiniMax-M2.7 |
| `minimax_m25` | openai | MiniMax-M2.5 |
| `glm_5` | openai | glm-5 |

### Environment variables

Each profile reads its credentials from dedicated env vars. Copy `.env.example` to `.env` and fill in the keys for the profiles you use:

```bash
# minimax_m27 (default profile)
LLM_MINIMAX_M27_API_KEY=your_key_here
# LLM_MINIMAX_M27_BASE_URL=https://api.minimax.io/anthropic  # optional, this is the default

# glm_5
LLM_GLM_5_API_KEY=your_key_here
LLM_GLM_5_BASE_URL=https://api.z.ai/api/paas/v4/
```

### Switching profiles

All CLI commands accept `--llm-profile`:

```bash
uv run python -m coder_agent run "fix the bug" --llm-profile glm_5
uv run python -m coder_agent chat --llm-profile minimax_m25
uv run python -m coder_agent eval --preset C4 --llm-profile glm_5 --config-label c4_glm5_smoke
```

The selected profile (name, model, transport) is recorded in every eval run manifest under `llm_profile`, `llm_model`, and `llm_transport` fields.

### Adding a new profile

Add an entry to `config.yaml` under `llm.profiles`:

```yaml
llm:
  default_profile: minimax_m27
  profiles:
    my_provider:
      transport: openai          # "openai" or "anthropic"
      model: my-model-name
      api_key_env: LLM_MY_PROVIDER_API_KEY
      base_url_env: LLM_MY_PROVIDER_BASE_URL
```

Then add the corresponding vars to `.env` and use `--llm-profile my_provider`.

## Evaluation and Re-Baselining

The branch currently has two relevant accepted baseline documents:

- [BASELINE_0_7_1.md](./report/BASELINE_0_7_1.md): current accepted `0.7.1` closure baseline for SWE promoted noise-reduction and attribution cleanup
- [BASELINE_0_6_0.md](./report/BASELINE_0_6_0.md): previous accepted runtime-contract baseline and the last full Custom/SWE closure before the `0.7.x` diagnostic cycle
- [REBASELINE_PLAYBOOK_0_7_1.md](./report/REBASELINE_PLAYBOOK_0_7_1.md): completed reproduction contract for the accepted `0.7.1` closure

For `v0.7.1`, public reporting should cite `swe_promoted_cmp_v071r1_C3`, `swe_promoted_cmp_v071r1_C6`, `swe_promoted_support_v071r1_C4`, and the matching manifests, trajectories, and analysis reports. The accepted result is that `C3`, `C4`, and `C6` all land at `2/8 = 25.0%` on the fixed `8`-task promoted SWE lane; `C4` remains supporting and is not promoted. This is a noise-reduction and attribution-cleanup closure, not a throughput uplift release.

The accepted `0.6.0` Custom compare artifacts remain `custom_v060_cmp_C3`, `custom_v060_cmp_C4`, and `custom_v060_cmp_C6`. Custom remains the low-cost daily regression suite; the fixed SWE-bench Lite subsets remain the higher-signal repository-repair smoke and compare lanes. They use checked-in generated official Lite metadata plus local runtime overrides and real `clone -> checkout -> diff -> test` semantics, with optional local mirrors for caching, but they are still a small fixed subset rather than a public full-dataset leaderboard claim.

Archived historical promoted artifacts (v0.4.0 baseline, Custom results against the original 21-task suite):

- HumanEval primary: `humaneval_040_final_c6` -> `161/164 = 98.2%`
- HumanEval supporting: `humaneval_040_final_c3` -> `157/164 = 95.7%`
- Custom primary: `custom_040_final_cmp_retry_C6` -> `21/21 = 100.0%`
- Custom supporting compare: `custom_040_final_cmp_C3` -> `20/21 = 95.2%`
- Custom supporting memory compare: `custom_040_final_cmp_retry_C4` -> `20/21 = 95.2%`
- Custom standalone memory reference: `custom_040_final_c4` -> `19/21 = 90.5%`

> **Note:** The Custom benchmark was expanded from 21 to 40 tasks in v0.4.3. The above Custom results are historical archive data and cannot be compared directly to the accepted `0.5.1` reruns on the current 40-task suite.

Important note:

- the first `custom_040_final_cmp` compare run produced a clean `C3` lane, but its `C4` and `C6` lanes were materially degraded by provider/API connection failures during `llm.chat`
- `custom_040_final_cmp_retry_C4` and `custom_040_final_cmp_retry_C6` supersede those polluted `C4`/`C6` compare artifacts and are now the accepted final compare metrics
- the original `custom_040_final_cmp_C4` and `custom_040_final_cmp_C6` artifacts are retained for audit/history only and are no longer part of the accepted final metric set

Historical reports remain available under [`report/`](./report/), but they are now archive/reference material rather than the current-branch source of truth.

Notes:

- `results/`, `trajectories/`, and `memory/` are local runtime outputs and are ignored by Git.
- For resumed runs, treat final `results/*.json` files as the metric source of truth.
- `*_run_manifest.json` captures both the preset `agent_config` and any runtime `experiment_config` overrides, plus `run_id`, `workspace_path`, `workspace_mode`, `task_ids`, and benchmark-specific metadata such as the SWE-bench Lite subset manifest hash and source mode.
- Trajectory files are primarily for debugging, failure inspection, and taxonomy analysis.

## Repository Structure

```text
coder_agent/
  cli/          command registry, REPL, and command modules
  core/         agent facade, runtime loop, context, session, LLM client
  eval/         benchmarks, runner facade, verification, analysis modules
  memory/       memory manager and trajectory store
  tools/        file, shell, and search tools
tests/          automated tests
report/         public experiment reports and re-baselining notes
config.yaml     runtime defaults
pyproject.toml  project metadata and dependencies
```

## Development

Run the test suite:

```bash
uv run pytest
```

Continuous integration checks:

- `uv run pytest`
- `uv run python -m coder_agent --help`

Default interactive mode:

```bash
uv run python -m coder_agent
```

Show available CLI commands:

```bash
uv run python -m coder_agent --help
```
