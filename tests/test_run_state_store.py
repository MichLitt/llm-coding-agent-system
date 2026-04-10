from coder_agent.memory.run_state import RunMetrics, RunStateStore


def test_run_state_store_records_run_lifecycle(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")

    store.create_run("run-1", "demo task", "cli")
    store.start_run("run-1")
    store.record_step(
        "run-1",
        0,
        thought="inspect file",
        observation="read ok",
        tool_call_count=1,
        had_error=False,
        step_tokens=12,
        step_duration_ms=34,
        loop_state={"steps": 1, "all_tool_calls": ["read_file"]},
    )
    store.record_tool_call(
        "run-1",
        0,
        tool_use_id="call-1",
        tool_name="read_file",
        args={"path": "demo.txt"},
        result_text="hello",
        is_error=False,
        error_kind=None,
        duration_ms=10,
    )
    store.finish_run(
        "run-1",
        "success",
        "model_stop",
        None,
        RunMetrics(total_steps=1, total_tool_calls=1, total_tokens=12, tool_success_rate=1.0),
    )

    run = store.get_run("run-1")
    steps = store.list_steps("run-1")
    checkpoint = store.latest_checkpoint("run-1")

    assert run is not None
    assert run["status"] == "success"
    assert run["termination_reason"] == "model_stop"
    assert run["total_tool_calls"] == 1
    assert len(steps) == 1
    assert steps[0]["thought_text"] == "inspect file"
    assert checkpoint is not None
    assert checkpoint["loop_state_json"]["steps"] == 1


def test_run_state_store_builds_resume_summary(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")

    store.create_run("run-2", "demo task", "cli")
    store.record_step(
        "run-2",
        0,
        thought="inspect tests",
        observation="found failure in tests/test_demo.py",
        tool_call_count=1,
        had_error=True,
        step_tokens=20,
        step_duration_ms=50,
        loop_state={"steps": 1},
    )

    summary = store.build_resume_summary("run-2")

    assert "completed 1 step" in summary
    assert "inspect tests" in summary


def test_run_state_store_get_resume_target_reports_resumable_checkpoint(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("run-3", "demo task", "cli")
    store.start_run("run-3")
    store.record_step(
        "run-3",
        1,
        thought="inspect app",
        observation="found failing branch",
        tool_call_count=1,
        had_error=False,
        step_tokens=10,
        step_duration_ms=20,
        loop_state={"steps": 2},
    )

    target = store.get_resume_target("run-3")

    assert target is not None
    assert target["resumable"] is True
    assert target["checkpoint"]["step_index"] == 1
    assert "Previous run completed" in target["resume_summary"]
