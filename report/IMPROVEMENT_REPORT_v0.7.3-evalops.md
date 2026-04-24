# Improvement Report: v0.7.3-evalops

## What Changed

Added fire-and-forget EvalOps reporting to close the Phase 2 integration loop
between `llm-coding-agent-system` and `llm-evalops-platform`.

### Files Added

- `coder_agent/evalops/__init__.py` — package init
- `coder_agent/evalops/schema.py` — `AgentRunReport` dataclass, mirrors the `agent/v1` ingest schema in the platform
- `coder_agent/evalops/client.py` — `EvalOpsClient` with fire-and-forget `submit()`, reads `EVALOPS_ENDPOINT` / `EVALOPS_API_KEY` from env

### Files Modified

- `coder_agent/core/agent_loop.py`
  - Added module-level `_try_report_to_evalops()` helper
  - Added 2-line call in `finalize_result` closure: `if run_id: _try_report_to_evalops(...)`

## Intended Effect on Agent Behavior

**None.** The evalops submit call runs entirely after `finish_run` completes, i.e., after the agent loop has already terminated and the final `TurnResult` is assembled. It is fire-and-forget: any exception is caught and logged at WARNING level, and never propagates to the caller. When `EVALOPS_ENDPOINT` is not set (default), the client is a strict no-op.

## Rebaseline Required?

**No.** Per CLAUDE.md rules, rebaseline is required for changes to agent loop logic that affect agent behavior. This change is eval/observability tooling:

- It runs after the agent terminates, not during agent execution
- It has no effect on tool selection, step progression, verification, or retry logic
- It is disabled by default (EVALOPS_ENDPOINT unset = no-op)
- All 266 existing tests pass unchanged

## How to Enable

Set in `.env` or environment:

```
EVALOPS_ENDPOINT=http://localhost:8000/v1/ingest/agent/v1
EVALOPS_API_KEY=          # optional
```

## How to Pass Eval Metadata

For benchmark runs that should participate in platform compare, set `task_metadata` when calling `run_agent_loop`:

```python
task_metadata={
    "benchmark_name": "swe_bench_lite",
    "task_ids": ["django-001", "flask-002"],
}
```

Without these keys the run is reported as `run_type="service"` with `task_set_id=None`, which is correct for interactive/service runs.
