from dataclasses import dataclass

from coder_agent.core.agent import Agent
from coder_agent.core.agent_types import ModelConfig
from coder_agent.memory.run_state import RunStateStore
from coder_agent.tools.base import Tool


@dataclass
class DummyTool(Tool):
    async def execute(self, **kwargs):
        return "ok"


class FakeClient:
    def __init__(self, response):
        self.response = response

    async def chat(self, **kwargs):
        return self.response

    async def aclose(self):
        return None


def test_agent_run_resume_uses_latest_checkpoint_and_continues_steps(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("run-resume", "demo task", "cli")
    store.start_run("run-resume")
    store.record_step(
        "run-resume",
        0,
        thought="inspect failing test",
        observation="found mismatch",
        tool_call_count=1,
        had_error=False,
        step_tokens=10,
        step_duration_ms=25,
        loop_state={
            "steps": 1,
            "all_tool_calls": ["read_file"],
            "successful_tool_calls": 1,
            "retry_count": 0,
            "retry_steps": 0,
            "verification_attempts": 0,
            "verification_failures": 0,
            "consecutive_verification_failures": 0,
            "recovery_mode": "none",
            "consecutive_identical_failures": 0,
            "doom_loop_warnings_injected": 0,
            "observations_compressed": 0,
            "compaction_events": 0,
            "tried_approaches": [],
            "approach_memory_injections": 0,
            "cross_task_memory_injected": False,
            "memory_injections": 0,
            "db_records_written": 0,
            "ad_hoc_install_count": 0,
        },
    )

    agent = Agent(
        tools=[DummyTool(name="read_file", description="read", input_schema={"type": "object"})],
        client=FakeClient({"content": [{"type": "text", "text": "done"}], "tool_uses": []}),
        model_config=ModelConfig(),
        run_state_store=store,
    )

    result = agent.run("", run_id="run-resume", resume=True, record_memory=False)
    steps = store.list_steps("run-resume")
    run = store.get_run("run-resume")

    assert result.success is True
    assert result.extra["run_id"] == "run-resume"
    assert [step["step_index"] for step in steps] == [0, 1]
    assert run is not None
    assert run["status"] == "success"
    assert run["total_steps"] == 2


def test_agent_run_resume_without_checkpoint_starts_from_stored_task(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("run-empty", "demo task", "cli")
    store.start_run("run-empty")

    agent = Agent(
        tools=[DummyTool(name="read_file", description="read", input_schema={"type": "object"})],
        client=FakeClient({"content": [{"type": "text", "text": "done"}], "tool_uses": []}),
        model_config=ModelConfig(),
        run_state_store=store,
    )

    result = agent.run("", run_id="run-empty", resume=True, record_memory=False)
    steps = store.list_steps("run-empty")

    assert result.success is True
    assert result.extra["resumed_task"] == "demo task"
    assert [step["step_index"] for step in steps] == [0]
