# Run Recovery Report — v0.8.0-run-recovery

Status: locally implemented and verified; not released or benchmark-accepted.

## Problem

Before this change:

- **Resume was approximate.** Checkpoints stored the step's thought and
  observation text, loop counters, and the tool-call audit, but not the message
  history. `run --resume` rebuilt context from the original task plus a
  `[Resume context]` message: the last 6 steps, thoughts cut to 160 characters
  and observations to 220. The model saw a different conversation after every
  resume, and nothing checked the workspace.
- **Runs had no owner.** Two processes could resume the same `running` run at
  once. Runs a dead process left `running` stayed `running` forever.
- **SQLite and the service were unguarded.** SQLite used the default rollback
  journal. The service started one thread per submit with no limit, and a
  retried submit created a duplicate run.

## What changed

- `coder_agent/core/checkpoint.py` (new): history snapshots (canonical JSON,
  zlib, schema `history/v1`, system-prompt SHA-256), an API-consistency check,
  and a metadata workspace fingerprint (path, size, mtime; caches skipped;
  capped at 20,000 files).
- `coder_agent/memory/run_state.py`:
  - **Migrations.** Additive, idempotent columns for snapshots, fingerprints,
    owner host/pid, heartbeat, start fingerprint and idempotency key, plus a
    unique partial index on the key.
  - **SQLite settings.** WAL with `synchronous=NORMAL`.
  - **Ownership.** `start_run` is now a compare-and-set claim, and
    `reconcile_orphans` marks dead-owner runs `interrupted`, which is a
    resumable status.
  - **Checkpoint helpers.** `latest_resumable_checkpoint` and
    `discard_steps_from`.
  - **Heartbeat.** It is written in the same transaction as each step.
- `coder_agent/core/agent_loop.py`, `agent.py`:
  - Every normal step end writes a snapshot and a fingerprint.
  - The exception path writes neither, because its history may hold an
    unanswered tool call.
  - A fresh run records its start fingerprint.
  - Resume selects a mode (below), and `result.extra["resume_mode"]` reports it.
  - A run owned by a live process is refused.
- `coder_agent/service/app.py`, `schemas.py`, `config.py`, `config.yaml`:
  - `service.max_concurrent_runs` (default 4); extra submits get `429` with
    `Retry-After`.
  - An `Idempotency-Key` with the same body replays the original run
    (`replayed: true`); a different body gets `409`.
  - On startup, the service reconciles runs left `running` by a dead process.
- `coder_agent/eval/crash_injection.py` (new): a crash-injection harness and
  campaign CLI.
- Tests: `tests/test_run_recovery.py` (new, 20 tests). One existing service
  test was updated for the new `submit_run` signature and response field.

## Resume modes

| Mode | When | History given to the model |
|---|---|---|
| `exact` | A usable step snapshot exists and the workspace matches it | Identical to the uninterrupted run |
| `exact_workspace_drift` | Snapshot exists, but the workspace changed after it or is unverifiable | Identical, plus one `[Resume notice]` |
| `restart` | No step was checkpointed, and the workspace matches its start state (or the run never started) | Identical to a fresh run |
| `restart_workspace_drift` | No step checkpoint, and the workspace changed since start | Fresh run plus one notice |
| `summary` | Legacy checkpoints without snapshots, a changed system prompt, or the C5 decomposer | Legacy text summary (inexact) |

## What a checkpoint stores

| Data | Purpose |
|---|---|
| Loop state JSON | Counters, retry/verification state, tried approaches, install budget, usage counters |
| History snapshot | Full message list, per-message token pairs and context estimate, with the system-prompt hash |
| Workspace fingerprint | Detects edits made after the checkpoint (e.g. by the step in flight) |
| Thought and observation text, step tokens, duration | Inspection and the legacy summary |
| Run row: status, owner, heartbeat | Liveness, claims, orphan detection |

The tool-call audit is written per call. It is kept even for steps a resume
supersedes, because those executions did happen.

## Serialization

### Snapshot format

A snapshot is canonical JSON (`sort_keys`), zlib-compressed, and tagged with
`schema: history/v1`. A corrupt or unknown-schema blob decodes to `None` and
falls back to `summary`; it never crashes the resume.

### Snapshots instead of an append-only log

Truncation, compaction and approach-memory injection all mutate history in
place. A replayable log would need those mutation events too, including the
LLM-written compaction summaries. A boundary snapshot restores in O(1).

The cost is storage. In-run context is currently unbounded (see the
token-usage report's known issue), so snapshot storage grows quadratically
with step count. Pruning to the latest K snapshots is a one-line retention
rule if storage becomes an issue.

### Write atomicity

The step row, its snapshot and the run heartbeat commit in one transaction.
Snapshots are taken only at step ends, where every tool call has its result,
and they are re-validated on restore.

## Concurrency

- **Within a process.** There is one SQLite connection serialized by a lock;
  writes are small. Service run threads share the store.
- **Across processes.** WAL lets the CLI and the service read while another
  process writes, and the connect timeout is SQLite's busy timeout.
- **Run claims.** `start_run` re-reads `(status, owner_host, owner_pid)` and
  updates only if they are unchanged, so concurrent resumers cannot both win.
- **Owner liveness.** On the same host, it is a pid check. On another host,
  it is a stale heartbeat (default 900 s, since steps can run for minutes).
- **Service admission.** The capacity check, row insert and thread start
  happen under one lock. A unique index on the idempotency key resolves a race
  between two simultaneous submits with the same key: the loser replays the
  winner.

## Semantics and limits

- **At-least-once tools.** The step in flight at a crash runs again.
  Idempotent edits (`write_file`, `patch_file`) are safe; non-idempotent shell
  side effects can repeat.
- **Lost usage.** Usage from the call in flight at the crash is billed but not
  recorded, so the run total is a lower bound in that case.
- **Fingerprint precision.** The fingerprint is metadata-based. It may report
  drift that is harmless, which only adds a notice. Content rewritten with
  identical size and mtime is not detected.
- **Pid reuse.** A recycled pid on the same host can make a dead owner look
  alive; resuming is then refused until the pid exits.
- **No service resume.** The service does not auto-resume `interrupted` runs;
  use `run --resume`.

## Verification

- `tests/test_run_recovery.py` (20 tests) covers:
  - snapshot round-trip and corrupt-payload fallback, API consistency,
    fingerprint behavior;
  - exact resume: the model requests after a resume equal those of an
    uninterrupted run;
  - a negative control showing the legacy summary resume is detectably
    different;
  - drift notices, restart modes, discarding superseded rows, claim refusal for
    a live owner, dead-owner reconciliation, remote-heartbeat staleness, WAL;
  - service `429`, idempotent replay and `409` conflict, startup
    reconciliation;
  - out-of-process abrupt exits at the LLM call, after a tool side effect and
    after a checkpoint commit, plus a real SIGKILL.
- Agent owner suite: 350 passed.
- **Crash-injection campaign** (seed 7, 50 runs, 5 steps each, committed as
  `artifacts/crash-injection/crash-injection-latest.json`):
  - 50/50 crashed and 50/50 passed every check: completion, files, step count,
    contiguous step rows, usage, and history.
  - Resume modes: 34 `exact`, 4 `exact_workspace_drift`, 8 `restart`,
    4 `restart_workspace_drift`.
  - An independent run (seed 2026, 60 runs, 8 steps) also passed 60/60.
- **What the campaign caught.** The first campaign passed only 38/50. All 12
  failures were crashes in step 1, before any checkpoint. Those runs fell back
  to `summary` with an empty summary, so a partial first-step edit went
  unreported. The `restart` modes and the start-of-run fingerprint baseline
  were added in response.

## Rebaseline decision

No rebaseline is required. For runs that are not resumed, model inputs are
unchanged: the change adds only checkpoint writes and fingerprints. Resumed
runs are not part of any benchmark lane; eval `--resume` operates at the task
level.
