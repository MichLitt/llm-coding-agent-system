# LLM Coding Agent System

LLM Coding Agent System is a ReAct-style coding agent for local software tasks. It combines tool use, trajectory logging, benchmark runners, and analysis utilities in one repo so the agent can be developed and evaluated with the same codebase.

License: [MIT](./LICENSE).

## Core Capabilities

- ReAct-style agent loop with tool calling and self-correction
- Task-aware verification gate for benchmark termination
- Custom benchmark runner for multi-step coding tasks
- HumanEval runner for function-level benchmark evaluation
- Trajectory analysis, failure taxonomy, and experiment comparison

## Current Best Results

The latest public results are summarized in [IMPROVEMENT_REPORT_v8.md](./report/IMPROVEMENT_REPORT_v8.md).

### HumanEval (164 tasks)

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Avg Tokens |
|--------|---------------:|-----------------:|---------------:|----------:|-----------:|
| C3 (react + correction) | 96.3% (158/164) | 100.0% (164/164) | 96.3% (158/164) | 3.05 | 410 |
| C5 (C4 + checklist) | 80.5% (132/164) | 95.7% (157/164) | 80.5% (132/164) | 3.03 | 397.5 |
| **C6 (C3 + verification gate, v8)** | **98.2% (161/164)** | 96.3% (158/164) | 96.3% (158/164) | **3.49** | **410** |

### Custom Benchmark (21-task v8 suite)

| Config | Benchmark Pass | Clean Completion | Strict Success | Avg Steps | Retry Cost |
|--------|---------------:|---------------:|----------:|-----------:|
| **C4 (react + correction + memory)** | **100.0% (21/21)** | **95.2% (20/21)** | **95.2% (20/21)** | 7.95 | 8.4% |
| C5 (C4 + checklist) | 90.5% (19/21) | 85.7% (18/21) | 85.7% (18/21) | **7.33** | 8.4% |
| C6 (C3 + verification gate) | 95.2% (20/21) | 90.5% (19/21) | 90.5% (19/21) | 8.10 | **4.7%** |

Key takeaways:

- `C6` is the strongest HumanEval configuration in benchmark pass at 98.2% (161/164).
- On the expanded 21-task Custom suite, `C4` is the strongest configuration by benchmark pass and strict success.
- `C5` remains the most step-efficient Custom configuration, but it gives up too much correctness on the harder v8 tasks.
- `C6` reduces retry cost on Custom tasks, but does not outperform `C4` on final task completion.
- `v8` fixed the previous `ImportError` failure on HumanEval_137, but exposed a new termination-quality issue on a small number of tasks that pass verification yet still end in `max_steps`.
- Full raw experiment artifacts are not committed to the public repo by default. Reproduce them locally if needed.

## Quick Start

### 1. Install

```bash
uv sync
cp .env.example .env
```

Set your model credentials in `.env`.

### 2. Run the agent on a single task

```bash
uv run python -m coder_agent run "Create a Flask API with user auth"
```

### 3. Run one benchmark task

```bash
uv run python -m coder_agent eval --benchmark custom --limit 1 --config-label demo
```

### 4. Analyze an experiment

```bash
uv run python -m coder_agent analyze demo
```

## Evaluation and Reports

Highlighted reports:

- [IMPROVEMENT_REPORT_v8.md](./report/IMPROVEMENT_REPORT_v8.md)
- [IMPROVEMENT_REPORT_v7.md](./report/IMPROVEMENT_REPORT_v7.md)
- [IMPROVEMENT_REPORT_v6.md](./report/IMPROVEMENT_REPORT_v6.md)
- [IMPROVEMENT_REPORT_v5.md](./report/IMPROVEMENT_REPORT_v5.md)

Additional archived plans and earlier reports remain under [`report/`](./report/).

Notes:

- `results/`, `trajectories/`, and `memory/` are local runtime outputs and are ignored by Git.
- For resumed runs, treat final `results/*.json` files as the metric source of truth.
- Trajectory files are primarily for debugging, failure inspection, and taxonomy analysis.
- The Custom v8 comparison includes a benchmark hardening fix for `custom_v8_010`; `C6` was re-run after that fix, while `C4/C5` retained successful runs on the same task.

## Repository Structure

```text
coder_agent/
  cli/          CLI entrypoints
  core/         agent loop, context, decomposer, LLM client
  eval/         benchmarks, runner, metrics, analysis
  memory/       memory manager and trajectory store
  tools/        file, shell, and search tools
tests/          automated tests
report/         public experiment reports and project notes
config.yaml     runtime defaults
pyproject.toml  project metadata and dependencies
```

## Development

Run the test suite:

```bash
uv run pytest
```

Continuous integration runs on GitHub Actions with Python 3.12 and checks:

- `uv run pytest`
- `uv run python -m coder_agent --help`

Show available CLI commands:

```bash
uv run python -m coder_agent --help
```
