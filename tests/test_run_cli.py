from click.testing import CliRunner

import coder_agent.cli.main as main_module
from coder_agent.cli.main import cli
from coder_agent.memory.run_state import RunStateStore


class FakeAgent:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.close_calls = 0

    def run(self, user_text: str, **kwargs):
        self.calls.append((user_text, kwargs))
        return type(
            "TurnResult",
            (),
            {
                "content": "ok",
                "steps": 2,
                "tool_calls": ["read_file"],
                "success": True,
                "retry_steps": 0,
                "total_tokens": 0,
                "trajectory_id": None,
                "final_status": "success",
                "termination_reason": "model_stop",
                "error_details": [],
                "extra": {"run_id": kwargs.get("run_id") or "run-demo-1"},
            },
        )()

    def close(self):
        self.close_calls += 1


def test_run_resume_without_task_uses_stored_task(monkeypatch, tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("run-123", "stored task", "cli")
    store.start_run("run-123")
    store.record_step(
        "run-123",
        0,
        thought="inspect file",
        observation="done",
        tool_call_count=1,
        had_error=False,
        step_tokens=10,
        step_duration_ms=10,
        loop_state={"steps": 1},
    )
    fake_agent = FakeAgent()
    runner = CliRunner()

    monkeypatch.setattr(main_module.cfg.agent, "enable_run_state", True)
    monkeypatch.setattr(main_module, "make_run_state_store", lambda: store)
    monkeypatch.setattr(main_module, "make_agent", lambda **kwargs: fake_agent)
    monkeypatch.setattr(main_module, "make_trajectory_store", lambda _: None)

    result = runner.invoke(cli, ["run", "--resume", "run-123"])

    assert result.exit_code == 0
    assert "resuming_run_id=run-123" in result.output
    assert "resuming_from_step=0" in result.output
    assert fake_agent.calls == [("stored task", {"run_id": "run-123", "resume": True})]
    assert fake_agent.close_calls == 1


def test_run_rejects_run_id_and_resume_together():
    runner = CliRunner()

    result = runner.invoke(cli, ["run", "--run-id", "one", "--resume", "two", "task"])

    assert result.exit_code != 0
    assert "--run-id and --resume are mutually exclusive" in result.output


def test_run_requires_task_without_resume():
    runner = CliRunner()

    result = runner.invoke(cli, ["run"])

    assert result.exit_code != 0
    assert "TASK is required unless --resume is used" in result.output


def test_runs_list_and_show_render_persisted_run(monkeypatch, tmp_path):
    db_path = tmp_path / "run_state.db"
    store = RunStateStore(db_path)
    store.create_run("run-abc12345", "demo task", "cli")
    store.start_run("run-abc12345")
    store.record_step(
        "run-abc12345",
        2,
        thought="patch app",
        observation="implemented fix",
        tool_call_count=2,
        had_error=False,
        step_tokens=20,
        step_duration_ms=30,
        loop_state={"steps": 3},
    )
    runner = CliRunner()

    monkeypatch.setattr(main_module.cfg.agent, "enable_run_state", True)
    monkeypatch.setattr(main_module, "make_run_state_store", lambda: RunStateStore(db_path))

    list_result = runner.invoke(cli, ["runs", "list", "--limit", "5"])
    show_result = runner.invoke(cli, ["runs", "show", "run-abc12345"])

    assert list_result.exit_code == 0
    assert "run-abc1" in list_result.output
    assert "task=demo task" in list_result.output
    assert show_result.exit_code == 0
    assert "run_id=run-abc12345" in show_result.output
    assert "latest_checkpoint.step=2" in show_result.output
    assert "next_command=coder-agent run --resume run-abc12345" in show_result.output
