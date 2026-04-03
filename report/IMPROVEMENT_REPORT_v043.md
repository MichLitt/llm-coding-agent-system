# v0.4.3 Improvement Report — Benchmark Expansion

**Date:** 2026-04-01
**Branch:** claude/frosty-mendeleev
**Author:** Claude (automated)

---

## Overview

v0.4.0 established baselines of Custom C6=100% (21/21) and HumanEval C6=98.2% (161/164).
Both benchmarks had reached saturation at the C6 configuration, making C1–C6 discrimination
impossible on the existing task set.

v0.4.3 addresses this by:

1. **Custom benchmark expansion** (21 → 40 tasks): 19 new software-engineering tasks
   that require debugging, refactoring, extending, and testing existing codebases.
2. **MBPP benchmark integration**: new `coder_agent/eval/benchmarks/mbpp.py` module
   providing 374 broad Python programming tasks from the HuggingFace sanitized split.
3. **20 new tests** validating benchmark integrity, MBPP module correctness, and CLI changes.

---

## Design Decisions

### Custom benchmark philosophy

All custom tasks follow the **Software Engineering (SE) philosophy**: the agent works with
pre-existing code (via `setup_files`) rather than implementing from scratch. This
distinguishes Custom from HumanEval (function completion) and MBPP (standalone functions).

Each new task must:
- Provide at least one `setup_file` (pre-existing buggy/incomplete/untested code)
- Verify correctness via shell commands (`python -m pytest ...`)
- Exercise realistic developer workflows: debug, refactor, extend, test-write

Algorithmic tasks (data structures, competitive programming) intentionally excluded —
those belong to HumanEval and MBPP.

### MBPP role

MBPP complements HumanEval's depth with **breadth**: 374 shorter problems covering
strings, math, lists, and basic algorithms. Designed for:
- Detecting regressions in broad Python capability
- Higher statistical power than HumanEval's 164 tasks
- Faster iteration (avg 10 steps per task vs 15 for HumanEval)

---

## Stage 1: Custom Benchmark Expansion

### New task counts

| Difficulty | Before | Added | After |
|-----------|--------|-------|-------|
| easy      | 3      | 0     | 3     |
| medium    | 15     | 10    | 25    |
| hard      | 3      | 9     | 12    |
| **total** | **21** | **19** | **40** |

### New medium tasks (custom_medium_006–015)

| Task ID | Name | Scenario |
|---------|------|----------|
| `custom_medium_006` | debug_threading_race | Fix race condition in SharedCounter (missing Lock) |
| `custom_medium_007` | add_context_manager_support | Add `__enter__`/`__exit__` to DBConnection |
| `custom_medium_008` | parse_structured_logs | JSONL log parser with malformed-line handling |
| `custom_medium_009` | add_error_handling | Add error handling + retry to HTTP client stub |
| `custom_medium_010` | fix_async_coroutine_bugs | Fix 3 async/await bugs in AsyncDownloader |
| `custom_medium_011` | write_unit_tests_for_module | Write 10+ tests for Invoice class |
| `custom_medium_012` | add_type_annotations_complex | Annotate untyped_utils.py to pass mypy --strict |
| `custom_medium_013` | implement_missing_methods | Implement BoundedQueue TODOs |
| `custom_medium_014` | fix_circular_imports | Break circular import between models/utils modules |
| `custom_medium_015` | refactor_callback_to_coroutine | Refactor callback DataFetcher to async/await |

### New hard tasks (custom_hard_004–012)

| Task ID | Name | Scenario |
|---------|------|----------|
| `custom_hard_004` | add_comprehensive_test_suite | Write 15+ tests for PaymentProcessor |
| `custom_hard_005` | refactor_god_class | Split 350-line ReportGenerator into 3 modules |
| `custom_hard_006` | add_observability_layer | Add logging + metrics to DataService |
| `custom_hard_007` | debug_memory_retention | Fix unbounded EventCache history + add unsubscribe |
| `custom_hard_008` | implement_undo_redo_system | Command-pattern undo/redo for text Editor |
| `custom_hard_009` | build_orm_query_builder | Chainable QueryBuilder for sqlite3 ORM base |
| `custom_hard_010` | add_rate_limiting_middleware | Token-bucket RateLimitMiddleware (thread-safe) |
| `custom_hard_011` | implement_plugin_loader | Plugin discovery + hot-reload via importlib |
| `custom_hard_012` | migrate_config_system | Flat→nested config migration with auto-detection |

### New setup files added (19 files)

```
buggy_thread_counter.py       test_thread_counter.py
db_connection.py               test_db_connection.py
sample_logs.jsonl              test_log_parser.py
api_client_stub.py             test_api_client.py
buggy_downloader.py            test_downloader.py
invoice.py                     untyped_utils.py
partial_queue.py               test_queue_impl.py
circular_models.py             circular_utils.py      test_circular_fix.py
sync_fetcher.py                test_async_fetcher.py
payment_processor.py           report_generator.py    test_report.py
data_service.py                test_observability.py
leaky_cache.py                 test_memory.py
text_editor.py                 test_editor_undo.py
orm_base.py                    test_orm.py
middleware.py                  test_middleware_rate.py
app_skeleton.py                test_plugins.py
old_config.py                  config_v2_schema.py    test_config_migration.py
```

---

## Stage 2: MBPP Benchmark Integration

### Module: `coder_agent/eval/benchmarks/mbpp.py`

| Component | Description |
|-----------|-------------|
| `MBPPTask` | Dataclass: `task_id`, `text`, `code`, `test_list`, `test_setup_code` |
| `MBPPBenchmark.load(limit)` | Download + cache from HuggingFace `mbpp/sanitized`, return `MBPPTask` list |
| `MBPPBenchmark.evaluate_solution(task, solution)` | Run assert-based tests via subprocess, return bool |
| `MBPPBenchmark.build_agent_prompt(task)` | Prompt includes description + assert tests |
| `MBPPBenchmark.to_task_spec(task)` | Convert to `TaskSpec` with `mbpp_official` verification contract |
| `MBPPBenchmark.evaluate_solution_from_metadata(metadata, solution)` | Verification hook entry point |

### Verification integration (`eval_verification.py`)

- `run_mbpp_check(task, workspace)`: reads `solution.py`, runs via `evaluate_solution_from_metadata`
- `verify_mbpp(task, workspace)`: returns `VerificationResult`
- `build_verification_hook`: dispatches on `mode == "mbpp_official"`
- `expected_checks`: returns 1 for both `humaneval` and `mbpp` benchmarks

### CLI (`run_ablation.py`)

```bash
# Run ablation on MBPP (default 60 tasks)
uv run python -m coder_agent.cli.run_ablation --benchmark mbpp

# Run on full 374 tasks
uv run python -m coder_agent.cli.run_ablation --benchmark mbpp --limit 374
```

### Dependency

Added to `pyproject.toml`: `datasets>=2.14.0` (HuggingFace datasets library).

---

## Stage 3: Tests

**File:** `tests/test_benchmark_expansion.py` — 20 tests, all passing.

| Category | Tests | Coverage |
|----------|-------|----------|
| Custom expansion | 9 | task count, uniqueness, setup files, max_steps, verification |
| MBPP module | 7 | load, prompt, TaskSpec structure, eval correct/wrong/empty |
| MBPP verification | 3 | missing file, correct solution, hook dispatch |
| CLI | 1 | --benchmark mbpp accepted |

**Full test suite (excluding pre-existing test_shell_tool.py env failures):**
- 147 tests passing (was 127 before v0.4.3)

---

## Expected Baseline Impact

### Custom benchmark

The new hard tasks are designed to discriminate C1–C6 configurations:

- **New hard tasks** involve multi-step SE work (refactoring, system design, complex debugging)
  that requires planning (C5) and verification loops (C4) to succeed reliably.
- Expected C6 pass rate on new hard tasks: **50–80%** (vs 100% on the original 21)
- Expected C1 pass rate on new hard tasks: **10–30%** (no correction, no planning)
- This provides the discriminability the saturated original suite lacked.

### MBPP

- C6 expected pass rate: **~85–90%** (based on similar benchmarks)
- Larger task count (374 vs 164) provides stronger statistical significance
- Config deltas expected to be similar to HumanEval but with less variance

---

## Analysis: Task Discriminability Prediction

Tasks ranked by estimated C1–C6 delta (highest discrimination first):

| Task | Why Discriminating |
|------|-------------------|
| `custom_hard_005` (refactor_god_class) | Multi-file coordination requires planning + verification |
| `custom_hard_009` (build_orm_query_builder) | Multiple chainable methods, easy to get wrong without loops |
| `custom_hard_010` (add_rate_limiting_middleware) | Thread safety requires careful reasoning |
| `custom_hard_008` (implement_undo_redo_system) | Pattern-based, needs correction loops |
| `custom_hard_011` (implement_plugin_loader) | importlib + hot-reload is non-trivial |
| `custom_medium_010` (fix_async_coroutine_bugs) | Three bugs; correction loops help |
| `custom_medium_015` (refactor_callback_to_coroutine) | Async refactor benefits from verification |

Tasks expected to be solvable by all configs (low discrimination, but still useful for regression):

| Task | Why Low Discrimination |
|------|----------------------|
| `custom_medium_013` (implement_missing_methods) | BoundedQueue is well-specified; C1 likely solves it |
| `custom_medium_007` (add_context_manager_support) | `__enter__`/`__exit__` is a known pattern |
| `custom_medium_006` (debug_threading_race) | Single bug, clear hint in description |

---

## Recommendations for Next Steps

1. **Run full ablation** on the 40-task custom suite with all C1–C6 presets to validate
   the discrimination hypothesis above.
2. **Tune max_steps** for tasks where C6 consistently fails even with high step budgets —
   may indicate task is too hard or description needs clarification.
3. **MBPP sampling strategy**: Consider stratifying the 374 tasks by difficulty
   (function length / assertion count) rather than random sampling.
4. **Consider adding setup verification check** at benchmark load time: run each setup file's
   test suite against the canonical solution to confirm tests pass before using in evaluation.
