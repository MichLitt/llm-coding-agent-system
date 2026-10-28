"""Persistent run state store for resumable agent runs."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT,
    experiment_id TEXT NOT NULL,
    preset TEXT,
    llm_profile TEXT,
    workspace_path TEXT,
    task_description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at REAL,
    finished_at REAL,
    total_steps INTEGER NOT NULL DEFAULT 0,
    total_tool_calls INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    tool_success_rate REAL,
    termination_reason TEXT,
    error_summary TEXT,
    git_commit TEXT,
    config_json TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS run_steps (
    step_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    step_index INTEGER NOT NULL,
    thought_text TEXT,
    observation_text TEXT,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    had_error INTEGER NOT NULL DEFAULT 0,
    step_tokens INTEGER NOT NULL DEFAULT 0,
    step_duration_ms INTEGER NOT NULL DEFAULT 0,
    loop_state_json TEXT,
    recorded_at REAL NOT NULL,
    UNIQUE(run_id, step_index)
);

CREATE INDEX IF NOT EXISTS idx_run_steps_run_id ON run_steps(run_id, step_index);

CREATE TABLE IF NOT EXISTS tool_calls (
    call_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    step_index INTEGER NOT NULL,
    tool_use_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT,
    result_text TEXT,
    is_error INTEGER NOT NULL DEFAULT 0,
    error_kind TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    recorded_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run_id ON tool_calls(run_id, step_index);
"""

# Additive columns applied to both new and pre-existing databases. All are
# nullable: NULL means "not recorded", which old rows also are.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "runs": {
        # Provider-reported usage. runs.total_tokens keeps its NOT NULL legacy
        # contract and is 0 when unreported; token_usage_status disambiguates.
        "input_tokens": "INTEGER",
        "output_tokens": "INTEGER",
        "llm_calls": "INTEGER",
        "token_usage_status": "TEXT",
        "estimated_cost_usd": "REAL",
        # Owning process and liveness, for claim-on-start and orphan recovery.
        "owner_host": "TEXT",
        "owner_pid": "INTEGER",
        "heartbeat_at": "REAL",
        # Workspace fingerprint when the run first started: the drift baseline
        # for a resume that has no step checkpoint yet.
        "start_workspace_fingerprint": "TEXT",
        # Service submit idempotency.
        "idempotency_key": "TEXT",
        "request_sha256": "TEXT",
    },
    "run_steps": {
        # Exact-resume payload; see coder_agent.core.checkpoint.
        "history_snapshot": "BLOB",
        "workspace_fingerprint": "TEXT",
    },
}

_POST_MIGRATION_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_idempotency_key
    ON runs(idempotency_key) WHERE idempotency_key IS NOT NULL;
"""

RESUMABLE_STATUSES = frozenset({"pending", "running", "failed", "timeout", "interrupted"})
DEFAULT_ORPHAN_STALE_AFTER_SECONDS = 900.0
_HOST = socket.gethostname()


class RunClaimError(RuntimeError):
    """Raised when a run is owned by another live process."""


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _truncate(text: Any, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def current_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


@dataclass(frozen=True)
class RunMetrics:
    total_steps: int
    total_tool_calls: int
    total_tokens: int
    tool_success_rate: float | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    llm_calls: int | None = None
    token_usage_status: str | None = None
    estimated_cost_usd: float | None = None


class RunStateStore:
    """SQLite-backed store for run lifecycle, step checkpoints, and tool audits."""

    def __init__(
        self,
        db_path: Path,
        *,
        orphan_stale_after_seconds: float = DEFAULT_ORPHAN_STALE_AFTER_SECONDS,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.orphan_stale_after_seconds = float(orphan_stale_after_seconds)
        # One connection serialized by a lock: writes are small, and WAL lets
        # other processes (CLI, service) read while this one writes. The
        # connect timeout is SQLite's busy timeout for cross-process locks.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._enable_wal()
        # Durable across process crashes; only an OS crash can lose the last
        # commits, which resume then treats as not-yet-checkpointed steps.
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self.init_db()

    def _enable_wal(self, attempts: int = 50) -> None:
        # Switching the journal mode needs an exclusive lock, and SQLite reports
        # "database is locked" at once instead of honoring the busy timeout. So
        # concurrent first opens retry briefly. The mode persists in the file.
        for attempt in range(attempts):
            try:
                if str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                    self._conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or attempt == attempts - 1:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def init_db(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            for table, columns in _ADDITIVE_COLUMNS.items():
                existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
                for column, column_type in columns.items():
                    if column in existing:
                        continue
                    try:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                    except sqlite3.OperationalError as exc:
                        # Another connection added it between the check and the ALTER.
                        if "duplicate column name" not in str(exc):
                            raise
            self._conn.executescript(_POST_MIGRATION_SQL)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_run(
        self,
        run_id: str,
        task_description: str,
        experiment_id: str,
        *,
        task_id: str | None = None,
        preset: str | None = None,
        llm_profile: str | None = None,
        workspace_path: str | None = None,
        git_commit: str | None = None,
        config_json: str | dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
    ) -> None:
        payload = config_json
        if payload is not None and not isinstance(payload, str):
            payload = _json_dumps(payload)
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runs (
                    run_id, task_id, experiment_id, preset, llm_profile, workspace_path,
                    task_description, status, git_commit, config_json, created_at,
                    idempotency_key, request_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    experiment_id,
                    preset,
                    llm_profile,
                    workspace_path,
                    task_description,
                    git_commit or current_git_commit(),
                    payload,
                    now,
                    idempotency_key,
                    request_sha256,
                ),
            )
            self._conn.commit()

    def find_run_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return self._decode_run_row(row)

    def _owner_gone(self, run: dict[str, Any], now: float) -> bool:
        """Whether a running run's owner can no longer make progress."""
        host, pid = run.get("owner_host"), run.get("owner_pid")
        if host == _HOST and pid is not None:
            return int(pid) != os.getpid() and not _process_alive(int(pid))
        # Another host (or a legacy row without an owner): only a stale
        # heartbeat is evidence. Steps can legitimately run for minutes.
        last_seen = run.get("heartbeat_at") or run.get("started_at") or run.get("created_at") or 0.0
        return now - float(last_seen) > self.orphan_stale_after_seconds

    def claim_blocker(self, run: dict[str, Any]) -> str | None:
        """Reason this process may not take over ``run``, or None when it may."""
        if run.get("status") != "running" or run.get("owner_pid") is None:
            return None
        if run.get("owner_host") == _HOST and int(run["owner_pid"]) == os.getpid():
            return None
        if self._owner_gone(run, time.time()):
            return None
        return (
            f"Run {run['run_id']} is currently running in process {run['owner_pid']} "
            f"on {run.get('owner_host')}."
        )

    def start_run(self, run_id: str, *, start_workspace_fingerprint: str | None = None) -> None:
        """Claim the run for this process and mark it running.

        The claim is a compare-and-set on the (status, owner) values that were
        checked, so two processes resuming the same run cannot both win.
        Raises RunClaimError when another live process owns the run.
        """
        for _ in range(3):
            run = self.get_run(run_id)
            if run is None:
                raise RunClaimError(f"Run {run_id} not found.")
            blocker = self.claim_blocker(run)
            if blocker:
                raise RunClaimError(blocker)
            now = time.time()
            with self._lock:
                cursor = self._conn.execute(
                    """
                    UPDATE runs
                    SET status = 'running',
                        started_at = COALESCE(started_at, ?),
                        finished_at = NULL,
                        error_summary = NULL,
                        owner_host = ?,
                        owner_pid = ?,
                        heartbeat_at = ?,
                        start_workspace_fingerprint = COALESCE(start_workspace_fingerprint, ?)
                    WHERE run_id = ? AND status IS ? AND owner_host IS ? AND owner_pid IS ?
                    """,
                    (
                        now, _HOST, os.getpid(), now, start_workspace_fingerprint,
                        run_id, run["status"], run.get("owner_host"), run.get("owner_pid"),
                    ),
                )
                self._conn.commit()
            if cursor.rowcount == 1:
                return
        raise RunClaimError(f"Run {run_id} changed concurrently while being claimed.")

    def reconcile_orphans(self) -> list[str]:
        """Mark running runs whose owner is gone as 'interrupted' (resumable)."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM runs WHERE status = 'running'").fetchall()
        now = time.time()
        interrupted: list[str] = []
        for row in rows:
            run = dict(row)
            if not self._owner_gone(run, now):
                continue
            with self._lock:
                cursor = self._conn.execute(
                    """
                    UPDATE runs
                    SET status = 'interrupted',
                        error_summary = 'Owner process exited before the run finished.'
                    WHERE run_id = ? AND status = 'running' AND owner_host IS ? AND owner_pid IS ?
                    """,
                    (run["run_id"], run.get("owner_host"), run.get("owner_pid")),
                )
                self._conn.commit()
            if cursor.rowcount == 1:
                interrupted.append(str(run["run_id"]))
        return interrupted

    def finish_run(
        self,
        run_id: str,
        status: str,
        termination_reason: str | None,
        error_summary: str | None,
        metrics: RunMetrics,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    finished_at = ?,
                    total_steps = ?,
                    total_tool_calls = ?,
                    total_tokens = ?,
                    tool_success_rate = ?,
                    termination_reason = ?,
                    error_summary = ?,
                    input_tokens = ?,
                    output_tokens = ?,
                    llm_calls = ?,
                    token_usage_status = ?,
                    estimated_cost_usd = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    time.time(),
                    int(metrics.total_steps),
                    int(metrics.total_tool_calls),
                    int(metrics.total_tokens),
                    metrics.tool_success_rate,
                    termination_reason,
                    _truncate(error_summary, 1000) if error_summary else None,
                    metrics.input_tokens,
                    metrics.output_tokens,
                    metrics.llm_calls,
                    metrics.token_usage_status,
                    metrics.estimated_cost_usd,
                    run_id,
                ),
            )
            self._conn.commit()

    def cancel_run(self, run_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE runs
                SET status = 'cancelled', finished_at = ?
                WHERE run_id = ? AND status IN ('pending', 'running', 'interrupted')
                """,
                (time.time(), run_id),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def record_step(
        self,
        run_id: str,
        step_index: int,
        *,
        thought: str,
        observation: str,
        tool_call_count: int,
        had_error: bool,
        step_tokens: int,
        step_duration_ms: int,
        loop_state: dict[str, Any],
        history_snapshot: bytes | None = None,
        workspace_fingerprint: str | None = None,
    ) -> None:
        """Write one step checkpoint and the run heartbeat in a single transaction."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO run_steps (
                    run_id, step_index, thought_text, observation_text, tool_call_count,
                    had_error, step_tokens, step_duration_ms, loop_state_json, recorded_at,
                    history_snapshot, workspace_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, step_index) DO UPDATE SET
                    thought_text = excluded.thought_text,
                    observation_text = excluded.observation_text,
                    tool_call_count = excluded.tool_call_count,
                    had_error = excluded.had_error,
                    step_tokens = excluded.step_tokens,
                    step_duration_ms = excluded.step_duration_ms,
                    loop_state_json = excluded.loop_state_json,
                    recorded_at = excluded.recorded_at,
                    history_snapshot = excluded.history_snapshot,
                    workspace_fingerprint = excluded.workspace_fingerprint
                """,
                (
                    run_id,
                    int(step_index),
                    _truncate(thought, 2000),
                    _truncate(observation, 2000),
                    int(tool_call_count),
                    int(bool(had_error)),
                    int(step_tokens),
                    int(step_duration_ms),
                    _json_dumps(loop_state),
                    now,
                    history_snapshot,
                    workspace_fingerprint,
                ),
            )
            self._conn.execute("UPDATE runs SET heartbeat_at = ? WHERE run_id = ?", (now, run_id))
            self._conn.commit()

    def record_tool_call(
        self,
        run_id: str,
        step_index: int,
        *,
        tool_use_id: str,
        tool_name: str,
        args: dict[str, Any] | None,
        result_text: str,
        is_error: bool,
        error_kind: str | None,
        duration_ms: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tool_calls (
                    run_id, step_index, tool_use_id, tool_name, args_json, result_text,
                    is_error, error_kind, duration_ms, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(step_index),
                    tool_use_id,
                    tool_name,
                    _json_dumps(args or {}),
                    _truncate(result_text, 4000),
                    int(bool(is_error)),
                    error_kind,
                    int(duration_ms),
                    time.time(),
                ),
            )
            self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._decode_run_row(row)

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._decode_run_row(row) for row in rows]

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT step_index, thought_text, observation_text, tool_call_count, had_error,
                       step_tokens, step_duration_ms, loop_state_json, recorded_at
                FROM run_steps
                WHERE run_id = ?
                ORDER BY step_index ASC
                """,
                (run_id,),
            ).fetchall()
        return [self._decode_step_row(row) for row in rows]

    def is_resumable_status(self, status: str | None) -> bool:
        return str(status or "") in RESUMABLE_STATUSES

    def latest_resumable_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        """Latest step that carries an exact-resume history snapshot."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT step_index, thought_text, observation_text, tool_call_count, had_error,
                       step_tokens, step_duration_ms, loop_state_json, recorded_at,
                       history_snapshot, workspace_fingerprint
                FROM run_steps
                WHERE run_id = ? AND history_snapshot IS NOT NULL
                ORDER BY step_index DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return self._decode_step_row(row)

    def discard_steps_from(self, run_id: str, step_index: int) -> int:
        """Drop step checkpoints at or after ``step_index`` (superseded by a resume).

        Tool-call audit rows are kept: those tool executions did happen.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM run_steps WHERE run_id = ? AND step_index >= ?",
                (run_id, int(step_index)),
            )
            self._conn.commit()
        return cursor.rowcount

    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT step_index, thought_text, observation_text, tool_call_count, had_error,
                       step_tokens, step_duration_ms, loop_state_json, recorded_at
                FROM run_steps
                WHERE run_id = ?
                ORDER BY step_index DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return self._decode_step_row(row)

    def get_resume_target(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        checkpoint = self.latest_checkpoint(run_id)
        status = str(run.get("status") or "")
        resumable = self.is_resumable_status(status)
        reason = None
        if not resumable:
            reason = f"Run {run_id} is already {status} and cannot be resumed."
        elif blocker := self.claim_blocker(run):
            resumable = False
            reason = blocker
        return {
            "run": run,
            "checkpoint": checkpoint,
            "resume_summary": self.build_resume_summary(run_id),
            "resumable": resumable,
            "resume_error": reason,
        }

    def build_resume_summary(self, run_id: str, *, limit: int = 6) -> str:
        steps = self.list_steps(run_id)
        if not steps:
            return ""
        selected = steps[-max(1, limit) :]
        lines = [f"Previous run completed {len(steps)} step(s) before interruption."]
        for step in selected:
            thought = " ".join(str(step["thought_text"] or "").split())[:160]
            observation = " ".join(str(step["observation_text"] or "").split())[:220]
            lines.append(f"Step {step['step_index']}: thought={thought or '(none)'}")
            if observation:
                lines.append(f"Observation: {observation}")
        return "\n".join(lines)

    def _decode_run_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        if result.get("config_json"):
            try:
                result["config_json"] = json.loads(result["config_json"])
            except json.JSONDecodeError:
                pass
        return result

    def _decode_step_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        raw_loop_state = result.get("loop_state_json")
        if raw_loop_state:
            try:
                result["loop_state_json"] = json.loads(raw_loop_state)
            except json.JSONDecodeError:
                pass
        return result


__all__ = ["RunMetrics", "RunStateStore", "current_git_commit"]
