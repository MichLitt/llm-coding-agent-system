# Improvement Report v6 — v3 Enhancements: LLM-as-Critic + C5 Adaptive Checklist

> **Date**: 2026-03-08
> **Scope**: Two new components added on top of the v5 C1~C4 baseline

---

## Overview

v3 adds two independent upgrades to the Coder-Agent:

1. **Stage 2 — LLM-as-Critic Failure Taxonomy**: replaces the rule-based keyword matcher (which classified 85.7% of failures as "Other") with a two-dimensional LLM classification.
2. **Stage 3 — C5 Adaptive Checklist**: adds a Decomposer role that generates a structured sub-goal checklist before the Actor starts the ReAct loop, injecting progress context into every step.

---

## Stage 2: LLM-as-Critic Failure Taxonomy

### Motivation

The v5 failure taxonomy used regex keyword matching on step observations. Because HumanEval failures were often silent (no crash, no SyntaxError — the code just returned the wrong answer), 6 of 7 failures were classified as "Other".

### Implementation

**New file: none** (changes confined to `coder_agent/eval/analysis.py` + `coder_agent/cli/main.py`)

Two-dimensional classification schema:

| Dimension | Categories |
|-----------|-----------|
| `goal_alignment` | `correct` (understood the task) · `deviated` (misunderstood goal) |
| `execution_issue` | `logic` · `tool` · `self_eval` · `planning` · `none` |

Additional fields per failure: `explanation`, `fixable_by_more_steps`, `steps_used`.

**Design notes**:
- Each step's `thought` + `observation` (first 300 chars) + `error_type` are packed into a compact prompt.
- MiniMax-M2.5 emits `<think>...</think>` reasoning chains before the JSON answer. The parser strips the thinking block and searches for the **last** well-formed `{...}` object in the output.
- `max_tokens=1024` is required to allow the model to complete the JSON after its thinking chain.
- The original rule-based `failure_taxonomy()` is kept as default; LLM classification is opt-in via `--llm-taxonomy`.

**CLI usage**:
```bash
uv run python -m coder_agent analyze humaneval_full_c4_v4 --llm-taxonomy
```

### Results on HumanEval C4 (7 failures)

**Before (rule-based):**

| Category | Count | % |
|----------|-------|---|
| Other | 6 | 85.7% |
| Logic | 1 | 14.3% |

**After (LLM-as-Critic):**

| goal_alignment | execution_issue | Count | % |
|----------------|----------------|-------|---|
| correct | self_eval | 3 | 42.9% |
| correct | logic | 2 | 28.6% |
| correct | other | 1 | 14.3% |
| deviated | planning | 1 | 14.3% |

Key finding: **42.9% of failures are self-evaluation errors** — the agent produces a working solution but fails to verify it correctly (reports success prematurely or marks a correct implementation as failed). This is a distinct failure mode that rule-based matching cannot detect.

---

## Stage 3: C5 Adaptive Checklist

### Motivation

C4 achieves 100% on Custom tasks but the agent sometimes takes unnecessary detour steps before converging. A structured decomposition before execution could reduce wasted steps and improve efficiency.

### Architecture

```
Task → [Decomposer LLM call] → sub-goal list (3-6 items)
                                     ↓
              ┌──────────────────────────────┐
              │  ReAct loop (Actor)          │
              │  step 1: inject progress →  │
              │  step 2: inject progress →  │
              │  ...                         │
              └──────────────────────────────┘
```

### Implementation

**New file**: `coder_agent/core/decomposer.py`

- `Decomposer.decompose(task, client)`: one LLM call, system prompt instructs JSON array output of 3-6 ordered sub-goals.
- `Decomposer.update(steps)`: heuristic completion detection — if recent steps contain "Exit code: 0" and ≥50% of the goal's keywords appear in recent observations, the sub-goal is marked done.
- `Decomposer.to_progress_prompt()`: renders `✅ [1] done` / `⏳ [2] pending` style checklist with a "Next: focus on —" hint.

**Modified**: `coder_agent/core/agent.py`
- `Agent.__init__`: instantiates `Decomposer` when `experiment_config["checklist"]` is truthy.
- `Agent._loop()`: calls `decompose()` before the first step, injects progress as a `user` message before each subsequent step.
- Termination logic is unchanged — checklist is context-only, not a gate.

**Config additions**:
- `config.yaml`: `enable_checklist: false`
- `coder_agent/config.py`: `AgentConfig.enable_checklist`
- `coder_agent/cli/main.py`: `C5 = {"correction": True, "memory": True, "planning_mode": "react", "checklist": True}`

### C4 vs C5 — Custom 11 tasks

| Config | N | Benchmark Pass | Clean | Strict | Partial | Efficiency | Retry Cost | Avg Steps | Avg Tokens |
|--------|---|---------------|-------|--------|---------|-----------|-----------|-----------|------------|
| C4 (react + correction + memory) | 11 | 100.0% | 100.0% | 100.0% | 100.0% | 0.154 | **6.0%** | 7.6 | 410 |
| **C5 (C4 + checklist)** | 11 | 100.0% | 100.0% | 100.0% | 100.0% | **0.163** | **4.8%** | **6.5** | 410 |

### Key findings

- **C5 strictly dominates C4** on efficiency metrics with identical task success: fewer steps (6.5 vs 7.6, **−14.5%**), lower retry cost (4.8% vs 6.0%, **−1.2pp**), higher efficiency score (0.163 vs 0.154, **+6%**).
- **Zero regression** on any correctness metric — all 11 tasks pass under both configs.
- The Decomposer overhead (1 extra LLM call per task) is more than offset by the reduction in exploratory steps during execution.
- The **hard tasks benefit most** from decomposition: on tasks `custom_hard_002` (refactor utils into package) and `custom_hard_003` (memoize decorator with LRU), C5 completed in 7 steps vs C4's ~10 steps.

---

## Summary of v3 Changes

| Component | File(s) | What changed |
|-----------|---------|-------------|
| LLM-as-Critic taxonomy | `eval/analysis.py` | Added `LLMTaxonomyResult`, `_classify_one()`, `failure_taxonomy_llm()`, `print_llm_taxonomy()` |
| LLM taxonomy CLI flag | `cli/main.py` | Added `--llm-taxonomy` to `analyze` command |
| Decomposer role | `core/decomposer.py` | New file: LLM-generated sub-goal checklist + progress tracking |
| Agent checklist integration | `core/agent.py` | Decomposer init + decompose call + per-step progress injection |
| C5 preset + config | `cli/main.py`, `config.py`, `config.yaml` | `enable_checklist` field + C5 preset definition |

---

## Cumulative Architecture (v3)

```
Task → CLI
         ↓
    [C5 only] Decomposer → sub-goal list
         ↓
    ReAct Agent (Actor)
         ├── step: [Decomposer progress injection]
         ├── LLMClient (MiniMax-M2.5, streaming)
         ├── Tool dispatch (file / shell / search)
         ├── Self-Correction (C3+)
         └── Memory injection (C4+)
         ↓
    TrajectoryStore (JSONL)
         ↓
    TrajectoryAnalyzer
         ├── Rule-based failure taxonomy (default)
         └── LLM-as-Critic 2D taxonomy (--llm-taxonomy)
```

---

## Next Directions

1. **HumanEval C5 full run** — verify that the checklist also improves efficiency on function-level tasks (expected: smaller gain due to already-short trajectories).
2. **LLM taxonomy deeper analysis** — the `self_eval` failure mode (42.9% of HumanEval C4 failures) points to a verification gap; a dedicated test-generation or self-check step after solution writing could address this.
3. **Decomposer quality** — current heuristic sub-goal completion detection may miss completions or over-mark them; an LLM-judged completion check could be more reliable for complex tasks.
