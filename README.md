# LLM Coding Agent System

LLM Coding Agent System is a ReAct-style coding agent for local software tasks. It combines tool use, trajectory logging, benchmark runners, and analysis utilities in one repo so the agent can be developed and evaluated with the same codebase.

License: [MIT](./LICENSE).

## Core Capabilities

- Interactive multi-turn CLI session via `coder-agent` / `python -m coder_agent`
- ReAct-style agent loop with tool calling and self-correction
- Task-aware verification gate for benchmark termination
- Custom benchmark runner for multi-step coding tasks
- HumanEval runner for function-level benchmark evaluation
- Trajectory analysis, failure taxonomy, and experiment comparison

## Current Status

The current branch has an accepted `0.5.1` baseline on the 40-task Custom suite. Eval auditability and runtime-config plumbing are part of that accepted state, and the active source of truth is `report/BASELINE_0_5_1.md`. The older `0.4.0` promotion set remains archived historical context on the 21-task Custom suite.

Key points:

- The supported runtime path is an OpenAI-compatible backend configured with `LLM_API_KEY` and optional `LLM_BASE_URL`.
- `model.provider` remains in config for compatibility and is informational only at runtime.
- The active day-to-day presets are `default`, `C3`, `C4`, and `C6`.
- The active rebaseline docs are `BASELINE_0_5_1.md` and `REBASELINE_PLAYBOOK_0_5_1.md`.
- Accepted `0.5.1` shipped benchmark lanes are `c4_m1_final` and `c6_baseline_final`.
- Accepted supporting final compare lanes include `c4_m3_final`, `c6_ctx1_final`, `c6_ctx2_final`, `c6_ctx3_final`, and `c6_ctx_all_final`.
- `C5` remains available for checklist experiments, but it is explicitly non-promoted.

Recommended reading:

- [BASELINE_0_5_1.md](./report/BASELINE_0_5_1.md)
- [REBASELINE_PLAYBOOK_0_5_1.md](./report/REBASELINE_PLAYBOOK_0_5_1.md)
- [IMPROVEMENT_REPORT_v0.5.1.md](./report/IMPROVEMENT_REPORT_v0.5.1.md)
- [BASELINE_0_4_0.md](./report/BASELINE_0_4_0.md) - archived historical baseline on the 21-task suite
- [REBASELINE_PLAYBOOK_0_4_0.md](./report/REBASELINE_PLAYBOOK_0_4_0.md) - archived historical playbook
- [IMPROVEMENT_SUMMARY.md](./report/IMPROVEMENT_SUMMARY.md) - archived `0.3.0` summary
- [BASELINE_0_4_0_RC.md](./report/BASELINE_0_4_0_RC.md) - archived RC baseline

### Preset Guidance

| Preset | Primary use | 0.5.1 cycle status |
|--------|-------------|--------------|
| `default` | Config-driven interactive use | Active |
| `C3` | ReAct + correction baseline | Supporting compare lane |
| `C4` | Multi-step tasks with memory | Accepted shipped baseline lane |
| `C5` | Checklist/decomposer experiments | Experimental |
| `C6` | Verification-gated ReAct baseline | Accepted shipped baseline lane |

## Quick Start

### 1. Install

```bash
uv sync
cp .env.example .env
```

Set `LLM_API_KEY` in `.env`. If your provider exposes an OpenAI-compatible endpoint at a custom URL, also set `LLM_BASE_URL`.

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

### 4. Run one benchmark task

```bash
uv run python -m coder_agent eval --benchmark custom --preset C4 --limit 1 --config-label demo
```

Runtime experiment overrides can be passed as JSON and are captured in the run manifest:

```bash
uv run python -m coder_agent eval --benchmark custom --preset C4 --config-label demo \
  --experiment-config '{"memory_lookup_mode":"similarity","keep_recent_turns":4}'
```

### 5. Analyze an experiment

```bash
uv run python -m coder_agent analyze demo
```

## Evaluation and Re-Baselining

The active `0.5.1` rebaseline contract is:

- the required final Custom reruns have been completed
- exact artifact names and matching manifests/trajectories are recorded in `report/BASELINE_0_5_1.md`
- run manifests record both preset config and runtime experiment overrides
- public reporting cites exact accepted artifact names
- the `0.4.0` promotion set remains archive/reference only

Exact commands, artifact naming, and release acceptance checks live in [REBASELINE_PLAYBOOK_0_5_1.md](./report/REBASELINE_PLAYBOOK_0_5_1.md).

The current branch status is recorded in [BASELINE_0_5_1.md](./report/BASELINE_0_5_1.md).

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
- `*_run_manifest.json` captures both the preset `agent_config` and any runtime `experiment_config` overrides.
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
