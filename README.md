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

Version `0.4.5` is the current release. The accepted v0.4.0 baseline was established on March 11, 2026 against a 21-task Custom suite. v0.4.3 expanded the Custom benchmark to 40 tasks and added MBPP support; the v0.4.0 baseline artifacts predate this expansion and are not directly comparable to runs on the current 40-task suite.

Key points:

- The supported runtime path is an OpenAI-compatible backend configured with `LLM_API_KEY` and optional `LLM_BASE_URL`.
- `model.provider` remains in config for compatibility, but is informational only in `0.4.0`.
- The active day-to-day presets are `default`, `C3`, `C4`, and `C6`.
- `C3`, `C4`, and `C6` are the only benchmark-candidate presets for `0.4.0`.
- `C5` remains available for checklist experiments, but it is explicitly non-promoted.

Recommended reading:

- [BASELINE_0_4_0.md](./report/BASELINE_0_4_0.md)
- [IMPROVEMENT_SUMMARY_0_4_0.md](./report/IMPROVEMENT_SUMMARY_0_4_0.md)
- [REBASELINE_PLAYBOOK_0_4_0.md](./report/REBASELINE_PLAYBOOK_0_4_0.md)
- [IMPROVEMENT_REPORT_v10.md](./report/IMPROVEMENT_REPORT_v10.md)
- [IMPROVEMENT_SUMMARY.md](./report/IMPROVEMENT_SUMMARY.md) - archived `0.3.0` summary
- [BASELINE_0_4_0_RC.md](./report/BASELINE_0_4_0_RC.md) - archived RC baseline

### Preset Guidance

| Preset | Primary use | 0.4.0 status |
|--------|-------------|--------------|
| `default` | Config-driven interactive use | Active |
| `C3` | ReAct + correction baseline | Benchmark candidate |
| `C4` | Multi-step tasks with memory | Benchmark candidate |
| `C5` | Checklist/decomposer experiments | Experimental |
| `C6` | Verification-gated ReAct baseline | Benchmark candidate |

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

### 5. Analyze an experiment

```bash
uv run python -m coder_agent analyze demo
```

## Evaluation and Re-Baselining

The public `0.4.0` benchmark contract is:

- run full `custom` and full `humaneval`
- use `C3`, `C4`, and `C6` only for promoted `0.4.0` benchmark tables
- cite final accepted artifacts by exact artifact name
- keep release-candidate artifacts as archive/reference only

Exact commands, artifact naming, and release acceptance checks live in [REBASELINE_PLAYBOOK_0_4_0.md](./report/REBASELINE_PLAYBOOK_0_4_0.md).

The current accepted `0.4.0` benchmark results are recorded in [BASELINE_0_4_0.md](./report/BASELINE_0_4_0.md).

Final promoted artifacts (v0.4.0 baseline, Custom results against the original 21-task suite):

- HumanEval primary: `humaneval_040_final_c6` -> `161/164 = 98.2%`
- HumanEval supporting: `humaneval_040_final_c3` -> `157/164 = 95.7%`
- Custom primary: `custom_040_final_cmp_retry_C6` -> `21/21 = 100.0%`
- Custom supporting compare: `custom_040_final_cmp_C3` -> `20/21 = 95.2%`
- Custom supporting memory compare: `custom_040_final_cmp_retry_C4` -> `20/21 = 95.2%`
- Custom standalone memory reference: `custom_040_final_c4` -> `19/21 = 90.5%`

> **Note:** The Custom benchmark was expanded from 21 to 40 tasks in v0.4.3. The above Custom results are from the pre-expansion suite and cannot be compared directly to runs on the current 40-task suite. New baselines against the 40-task suite have not yet been promoted.

Important note:

- the first `custom_040_final_cmp` compare run produced a clean `C3` lane, but its `C4` and `C6` lanes were materially degraded by provider/API connection failures during `llm.chat`
- `custom_040_final_cmp_retry_C4` and `custom_040_final_cmp_retry_C6` supersede those polluted `C4`/`C6` compare artifacts and are now the accepted final compare metrics
- the original `custom_040_final_cmp_C4` and `custom_040_final_cmp_C6` artifacts are retained for audit/history only and are no longer part of the accepted final metric set

Historical reports remain available under [`report/`](./report/), but they are now archive/reference material rather than the current-branch source of truth.

Notes:

- `results/`, `trajectories/`, and `memory/` are local runtime outputs and are ignored by Git.
- For resumed runs, treat final `results/*.json` files as the metric source of truth.
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
