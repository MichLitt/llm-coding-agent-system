from types import SimpleNamespace
import sqlite3

from coder_agent.memory.manager import MemoryManager, _extract_keywords


def test_init_db_migrates_legacy_task_history_schema(tmp_path):
    db_path = tmp_path / "agent_memory.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            workspace_path TEXT UNIQUE NOT NULL,
            last_accessed TIMESTAMP NOT NULL
        );

        CREATE TABLE task_history (
            task_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            description TEXT NOT NULL,
            success INTEGER NOT NULL,
            steps INTEGER NOT NULL,
            tool_calls TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(project_id)
        );
        """
    )
    conn.commit()
    conn.close()

    memory = MemoryManager(db_path)
    columns = {
        row["name"]
        for row in memory._conn.execute("PRAGMA table_info(task_history)").fetchall()
    }

    assert "termination_reason" in columns
    assert "error_summary" in columns

    memory.close()


def test_record_task_persists_failure_metadata(tmp_path):
    memory = MemoryManager(tmp_path / "agent_memory.db")
    project_id = memory.get_or_create_project(tmp_path)
    result = SimpleNamespace(
        success=False,
        steps=3,
        tool_calls=["run_command"],
        termination_reason="max_steps",
        error_details=["first failure", "second failure"],
    )

    memory.record_task(project_id, "debug task", result)
    recent = memory.get_recent_tasks(project_id, n=1)

    assert len(recent) == 1
    assert recent[0]["description"] == "debug task"
    assert recent[0]["success"] is False
    assert recent[0]["termination_reason"] == "max_steps"
    assert recent[0]["tool_calls"] == ["run_command"]
    assert "first failure" in recent[0]["error_summary"]

    memory.close()


def test_extract_keywords_filters_stopwords_and_short_tokens():
    keywords = _extract_keywords("fix async coroutine bugs in downloader")

    assert keywords == {"async", "coroutine", "bugs", "downloader"}


def test_get_similar_tasks_returns_overlap_ranked_matches(tmp_path):
    memory = MemoryManager(tmp_path / "agent_memory.db")
    project_id = memory.get_or_create_project(tmp_path)

    def result(success=True, termination_reason="verification_passed", error_details=None):
        return SimpleNamespace(
            success=success,
            steps=3,
            tool_calls=["run_command"],
            termination_reason=termination_reason,
            error_details=error_details or [],
        )

    memory.record_task(project_id, "fix async coroutine bugs in downloader", result())
    memory.record_task(project_id, "fix async coroutine downloader cleanup", result())
    memory.record_task(project_id, "fix async downloader timeout handling", result())
    memory.record_task(project_id, "write downloader retry policy", result(success=False))

    similar = memory.get_similar_tasks(project_id, "fix async coroutine bugs in downloader", n=3)

    assert [task["description"] for task in similar] == [
        "fix async coroutine bugs in downloader",
        "fix async coroutine downloader cleanup",
        "fix async downloader timeout handling",
    ]
    assert all(isinstance(task["success"], bool) for task in similar)

    memory.close()
