"""Long-term memory backed by a local SQLite database.

Stores project metadata, file summaries, and task history across sessions.
No external database service required — sqlite3 is part of the Python stdlib.

Usage
-----
    from utils.memory_manager import MemoryManager
    import config

    memory = MemoryManager(config.MEMORY_DB_PATH)
    project_id = memory.get_or_create_project(config.WORKSPACE)
    memory.record_task(project_id, "create hello.py", result)
    tasks = memory.get_recent_tasks(project_id, n=5)
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
    last_modified INTEGER NOT NULL,   -- file mtime as Unix timestamp
    summary       TEXT,               -- short description (filled by LLM or user)
    PRIMARY KEY (project_id, file_path),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS task_history (
    task_id     TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    description TEXT NOT NULL,
    success     INTEGER NOT NULL,  -- 0 or 1
    steps       INTEGER NOT NULL,
    tool_calls  TEXT NOT NULL,     -- JSON list of tool names
    created_at  TIMESTAMP NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS preferences (
    project_id TEXT PRIMARY KEY,
    notes      TEXT,               -- free-form notes written by agent or user
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
"""


def _project_id(workspace: Path) -> str:
    """Derive a stable project ID from the workspace path."""
    return hashlib.sha1(str(workspace.resolve()).encode()).hexdigest()[:16]


class MemoryManager:
    """SQLite-backed memory for the Coder-Agent.

    All operations are synchronous (sqlite3 is not async). Call from the
    agent's synchronous context or use asyncio.to_thread() if needed.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_db()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """Create tables if they don't exist yet (idempotent)."""
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def get_or_create_project(self, workspace: Path) -> str:
        """Return the project_id for the given workspace, creating a record if new.

        Parameters
        ----------
        workspace : Path
            Absolute path to the workspace directory.

        Returns
        -------
        str
            A 16-char hex project ID derived from the workspace path.
        """
        pid = _project_id(workspace)
        now = datetime.now(timezone.utc).isoformat()

        existing = self._conn.execute(
            "SELECT project_id FROM projects WHERE project_id = ?", (pid,)
        ).fetchone()

        if existing:
            # Update last_accessed timestamp
            self._conn.execute(
                "UPDATE projects SET last_accessed = ? WHERE project_id = ?",
                (now, pid),
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
        """Persist the outcome of a completed agent turn.

        Parameters
        ----------
        project_id  : str
            ID returned by get_or_create_project().
        description : str
            The user's original task string.
        result      : TurnResult
            The TurnResult dataclass from agent.py; accessed via attributes
            result.success, result.steps, result.tool_calls.
        """
        task_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO task_history
               (task_id, project_id, description, success, steps, tool_calls, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                project_id,
                description,
                int(result.success),
                result.steps,
                json.dumps(result.tool_calls),
                now,
            ),
        )
        self._conn.commit()

    def get_recent_tasks(self, project_id: str, n: int = 5) -> list[dict[str, Any]]:
        """Return the n most recent tasks for this project.

        Parameters
        ----------
        project_id : str
        n          : int
            How many recent records to return (default 5).

        Returns
        -------
        list of dicts with keys: description, success, steps, tool_calls, created_at
        """
        rows = self._conn.execute(
            """SELECT description, success, steps, tool_calls, created_at
               FROM task_history
               WHERE project_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
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
    # File summaries (optional — filled by LLM or user)
    # ------------------------------------------------------------------

    def upsert_file_summary(self, project_id: str, file_path: str, mtime: int, summary: str = "") -> None:
        """Insert or update a file's summary record.

        Parameters
        ----------
        project_id : str
        file_path  : str  Relative path inside workspace.
        mtime      : int  File modification time as Unix timestamp.
        summary    : str  Optional human/LLM-written description.
        """
        self._conn.execute(
            """INSERT OR REPLACE INTO file_summaries
               (project_id, file_path, last_modified, summary)
               VALUES (?, ?, ?, ?)""",
            (project_id, file_path, mtime, summary),
        )
        self._conn.commit()

    def get_file_summary(self, project_id: str, file_path: str) -> dict[str, Any] | None:
        """Return stored metadata for a file, or None if not indexed.

        Returns
        -------
        dict with keys: file_path, last_modified, summary — or None
        """
        row = self._conn.execute(
            """SELECT file_path, last_modified, summary
               FROM file_summaries
               WHERE project_id = ? AND file_path = ?""",
            (project_id, file_path),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    def get_notes(self, project_id: str) -> str:
        """Return free-form notes for this project (empty string if none)."""
        row = self._conn.execute(
            "SELECT notes FROM preferences WHERE project_id = ?", (project_id,)
        ).fetchone()
        return row["notes"] or "" if row else ""

    def set_notes(self, project_id: str, notes: str) -> None:
        """Upsert project notes (agent or user can write here)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO preferences (project_id, notes) VALUES (?, ?)",
            (project_id, notes)
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
