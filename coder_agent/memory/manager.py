"""Long-term memory backed by a local SQLite database.

Stores project metadata, file summaries, task history, and experiment records.
"""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    workspace_path TEXT UNIQUE NOT NULL,
    last_accessed  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS file_summaries (
    project_id    TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    last_modified INTEGER NOT NULL,
    summary       TEXT,
    PRIMARY KEY (project_id, file_path),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS task_history (
    task_id     TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    description TEXT NOT NULL,
    success     INTEGER NOT NULL,
    steps       INTEGER NOT NULL,
    tool_calls  TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS preferences (
    project_id TEXT PRIMARY KEY,
    notes      TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    git_commit    TEXT,
    config        TEXT,
    timestamp     TIMESTAMP NOT NULL,
    results_path  TEXT
);
"""


def _project_id(workspace: Path) -> str:
    return hashlib.sha1(str(workspace.resolve()).encode()).hexdigest()[:16]


class MemoryManager:
    """SQLite-backed memory for the Coder-Agent."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def get_or_create_project(self, workspace: Path) -> str:
        pid = _project_id(workspace)
        now = datetime.now(timezone.utc).isoformat()
        existing = self._conn.execute(
            "SELECT project_id FROM projects WHERE project_id = ?", (pid,)
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE projects SET last_accessed = ? WHERE project_id = ?", (now, pid)
            )
        else:
            self._conn.execute(
                "INSERT INTO projects (project_id, workspace_path, last_accessed) VALUES (?, ?, ?)",
                (pid, str(workspace.resolve()), now),
            )
        self._conn.commit()
        return pid

    # ------------------------------------------------------------------
    # Task history
    # ------------------------------------------------------------------

    def record_task(self, project_id: str, description: str, result: Any) -> None:
        task_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO task_history
               (task_id, project_id, description, success, steps, tool_calls, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, project_id, description, int(result.success), result.steps,
             json.dumps(result.tool_calls), now),
        )
        self._conn.commit()

    def get_recent_tasks(self, project_id: str, n: int = 5) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT description, success, steps, tool_calls, created_at
               FROM task_history WHERE project_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (project_id, n),
        ).fetchall()
        return [
            {
                "description": r["description"],
                "success": bool(r["success"]),
                "steps": r["steps"],
                "tool_calls": json.loads(r["tool_calls"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # File summaries
    # ------------------------------------------------------------------

    def upsert_file_summary(self, project_id: str, file_path: str, mtime: int, summary: str = "") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO file_summaries (project_id, file_path, last_modified, summary) VALUES (?, ?, ?, ?)",
            (project_id, file_path, mtime, summary),
        )
        self._conn.commit()

    def get_file_summary(self, project_id: str, file_path: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT file_path, last_modified, summary FROM file_summaries WHERE project_id = ? AND file_path = ?",
            (project_id, file_path),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    def get_notes(self, project_id: str) -> str:
        row = self._conn.execute(
            "SELECT notes FROM preferences WHERE project_id = ?", (project_id,)
        ).fetchone()
        return row["notes"] or "" if row else ""

    def set_notes(self, project_id: str, notes: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO preferences (project_id, notes) VALUES (?, ?)",
            (project_id, notes),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Experiments (eval use)
    # ------------------------------------------------------------------

    def record_experiment(self, experiment_id: str, git_commit: str, config: dict, results_path: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO experiments (experiment_id, git_commit, config, timestamp, results_path) VALUES (?, ?, ?, ?, ?)",
            (experiment_id, git_commit, json.dumps(config), now, results_path),
        )
        self._conn.commit()

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["config"] = json.loads(d["config"])
        return d

    def list_experiments(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT experiment_id, git_commit, timestamp, results_path FROM experiments ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()
