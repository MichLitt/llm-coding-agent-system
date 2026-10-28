# Token Usage Accounting Report — v0.8.0-token-usage

Status: locally implemented and verified; not released or benchmark-accepted.

## Problem

Before this change, no reported token figure was provider usage:

- neither backend in `coder_agent/core/llm_client.py` read the provider's
  `usage`, and no caller passed `usage=` to `MessageHistory.add_message`;
- `MessageHistory.total_tokens` therefore stayed at its initial
  `len(system_prompt) // 4` estimate for the whole run;
- that value was reported as `total_tokens` in `TurnResult`, eval results,
  trajectories, conversation turn artifacts, run state and the EvalOps
  `agent/v1` report.

Evidence: all 18 v0.8 ComplexCodeBench runs record exactly 968 tokens
regardless of step count, and rebuilding the system prompt for that run's
workspace path gives `len(system_prompt) // 4 == 968`. The G3 evidence averages
850 and 867 "tokens" per task even though the candidate arm used fewer steps
(203 vs 230).

## What changed

- `coder_agent/core/usage.py` (new): `LLMUsage`, `UsageTotals`, provider usage
  normalizers, run/step attribution helpers and cost estimation.
- `coder_agent/core/llm_client.py`: the OpenAI-compatible backend requests
  `stream_options.include_usage` and reads usage from any stream chunk; if the
  provider rejects `stream_options` it retries once without it and stops
  sending it for that client. The Anthropic backend reads
  `final_message.usage` (input = uncached + cache-write + cache-read). Both
  return `usage` (or `None`); the `LLMClient` facade accumulates all completed
  calls, including context-compaction summaries.
- `coder_agent/config.py`, `config.yaml`: optional per-profile `pricing`
  (USD per million tokens). No prices are checked in; cost stays `null` until
  configured.
- `coder_agent/core/agent_loop.py`, `agent_run_context.py`, `agent_types.py`,
  `agent.py`: a run snapshots the client's totals at start, so usage is
  attributed per run and per step. `TurnResult.usage` carries
  `UsageTotals.to_report()`; `TurnResult.total_tokens` is provider input +
  output tokens or `None`. Checkpoint `loop_state` stores raw usage counters,
  and a resumed run adds usage recorded before the interruption.
- `coder_agent/memory/run_state.py`: additive, idempotent migration adds
  nullable `input_tokens`, `output_tokens`, `llm_calls`, `token_usage_status`
  and `estimated_cost_usd` to `runs`. `runs.total_tokens` keeps its legacy
  `NOT NULL` contract and is 0 when usage is unavailable; the status column
  disambiguates. `run_steps.step_tokens` is now provider tokens billed during
  that step.
- `coder_agent/service/schemas.py`: the run record exposes the new columns.
- `coder_agent/evalops/schema.py`: `agent/v1` adds `input_tokens`,
  `output_tokens`, `llm_call_count`, `token_usage_status`,
  `estimated_cost_usd`; `total_tokens` is `null` when unreported. The consumer
  side changed in the same task (`llm-evalops-platform` schema, adapter, docs,
  tests).
- `coder_agent/eval/*`, `memory/trajectory.py`: eval results gain a `usage`
  field; averages skip unreported runs and print `n/a` instead of a fake 0.
- `tests/test_usage_accounting.py` (new, 16 tests): normalizers, cost, both
  backends, the `stream_options` fallback, client accumulation, per-run and
  per-step attribution with leakage protection, null semantics, resume
  carry-over and the legacy-database migration.

## Status semantics

| `token_usage_status` | Meaning | Token totals / cost |
|---|---|---|
| `provider` | Every LLM call returned usage | Exact provider figures |
| `partial` | Some calls returned usage | Lower bounds |
| `unavailable` | No call returned usage | `null` (never estimated) |

A call that raises, for example a stream timeout, is counted as attempted with
unreported usage, so the run reads `partial`. The tokens it consumed are not
observable from the client.

This was added after the WP2 Custom-40 runs (`EVAL_INTEGRITY_AUDIT_v0.8.0.md`,
Finding 3). Before it, an aborted call was not counted at all, and a run with
one could still read `provider`.

## Rebaseline decision

No rebaseline is required for pass-rate metrics. The change does not alter
prompts, tools, loop decisions, verification, or context management.
`MessageHistory.total_tokens`, the value truncation reads, is deliberately left
unchanged.

All token and cost figures produced before this change are invalid and must not
be compared with or cited alongside new figures. This includes the G3 evidence
(see `docs/reports/agent-rag-ablation-v1-erratum.md` in the parent workspace)
and the unaccepted v0.8 baseline snapshots under `artifacts/v080-baseline/`.
Their pass/fail fields remain valid. Re-collect the v0.8 baselines with this
accounting before writing `BASELINE_0_8_0.md`.

## Known issue left unchanged

Because `MessageHistory.total_tokens` never accumulates, token-budget
truncation (`context_window_tokens`, 180,000) has never triggered. The other
two mechanisms are also off in every accepted configuration:

- History compaction runs only when `history_compaction_mode` is `semantic`,
  but the checked-in default is `rule_based`, which is a no-op.
- `observation_compression_mode` defaults to `off`, and no preset changes
  either setting.

In-run context is therefore unbounded and limited only by `max_steps`. Note
also that the original design summed every call's `input_tokens`, which
overstates context occupancy. Fixing context management changes Agent behavior
and needs its own change and rebaseline; it is out of scope here.

## Verification

- `tests/test_usage_accounting.py`: 16 passed.
- Agent owner suite: 330 passed (`uv run pytest`).
- EvalOps suite: 53 passed, including new ingest tests for usage metrics, null
  usage and status validation.
- RAG suite: 423 passed, 1 optional-model skip (`scripts/check_core_projects.sh`).
- Three-project closure: passed (`AGENT_EVALOPS_GATE=promoted`). The Agent
  reports carry the new nullable fields over HTTP.
- Live `glm_5` (`glm-5.1`) check, 2026-10-27:
  - The endpoint accepts `stream_options`, so the fallback path is not used.
  - For an identical request, the streamed usage captured by `LLMClient` equals
    the non-streamed response usage: 150 prompt, 3 completion, 128 cached once
    the prompt cache is warm.
  - One real Custom task (`custom_easy_001`, C3, label `wp0_usage_check`)
    recorded 2 calls, 3,415 input / 316 output / 1,472 cached tokens, 3,731
    total, status `provider`. The result JSON and the run-state row agree, and
    the per-step tokens (1,571 + 2,160) sum to the run total. The legacy
    estimate for the same kind of run was 968.
- The `minimax_*` profiles were not exercised live.
