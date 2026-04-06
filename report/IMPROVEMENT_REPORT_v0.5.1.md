# Improvement Report v0.5.1 — Eval Auditability & Memory Runtime Defaults

**Date:** 2026-04-05
**Tag:** `v0.5.1`
**Related plan:** `report/IMPROVEMENT_PLAN_v0.5.0.md`

---

## 1. What Changed

### Files modified

| Area | Files | Change |
|---|---|---|
| Eval CLI / manifests | `coder_agent/cli/eval.py`, `coder_agent/eval/runner.py`, `coder_agent/eval/ablation.py`, `coder_agent/cli/run_ablation.py` | Added `--experiment-config`, threaded runtime experiment config through eval/ablation entrypoints, and recorded it in run manifests |
| Runtime behavior | `coder_agent/core/agent_run_context.py`, `coder_agent/core/agent_loop.py`, `coder_agent/core/agent.py` | Fixed runtime override lookup for memory retrieval and compaction settings; added `memory_injections` / `db_records_written` activation counters; gated approach-memory injection to memory-enabled runs |
| Defaults / docs | `config.yaml`, `coder_agent/config.py`, `README.md`, `report/REBASELINE_PLAYBOOK_0_5_1.md`, `report/BASELINE_0_5_1.md` | Synced default config fields with code and documented runtime experiment snapshots |

### Default config updates

The following defaults are now declared in `config.yaml` and aligned with `config.py`:

- `agent.enable_approach_memory: true`
- `agent.memory_lookup_mode: recency`
- `agent.doom_loop_threshold: 2`
- `context.history_compaction_mode: rule_based`
- `context.history_compaction_message_threshold: 20`
- `context.keep_recent_turns: 6`

---

## 2. Intended Effect on Agent Behavior

- Eval runs are now auditable: each run manifest stores both the preset `agent_config` and the runtime `experiment_config` overrides used for that run.
- `eval` and `run_ablation` now support the same runtime override model, eliminating the gap where experiment flags existed in code but were not represented in the main CLI path.
- Memory retrieval mode now correctly respects runtime overrides instead of reading only the preset config path.
- History compaction now respects runtime `keep_recent_turns` overrides.
- Activation counters now distinguish:
  - within-task approach-memory injections
  - cross-task memory prompt injections
  - DB writes performed during eval
- Approach-memory injection is now limited to memory-enabled runs so enabling it in defaults does not silently alter non-memory baselines like `C6`.

---

## 3. Rebaseline Required?

**Yes.**

This change affects agent behavior and benchmark artifacts because it modifies:

- agent loop behavior (`coder_agent/core/agent_loop.py`)
- memory/runtime context behavior (`coder_agent/core/agent_run_context.py`, `coder_agent/core/agent.py`)
- evaluation/runtime configuration plumbing (`coder_agent/cli/eval.py`, `coder_agent/eval/runner.py`)

Per the repo baseline integrity rules, new accepted C4/C6 artifacts are required before promoting a fresh public baseline.

This report does **not** establish a new accepted baseline by itself. It prepared the codebase for the final `v0.5.1` C4/C6 reruns by making the resulting artifacts reproducible and auditable. The accepted final metric set is recorded separately in `report/BASELINE_0_5_1.md`.

---

## 4. Verification

Targeted regression suite passed:

```bash
uv run pytest tests/test_cli_eval.py tests/test_eval_runner.py tests/test_agent_termination.py tests/test_context_compression.py tests/test_config.py
```

Coverage added/updated for:

- `eval --experiment-config` parsing and validation
- passing `config_label` and runtime overrides into `make_agent()`
- manifest inclusion of runtime experiment config
- runtime override precedence for `memory_lookup_mode`
- `db_records_written` and `memory_injections` counter behavior
