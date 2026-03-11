from dataclasses import dataclass
import builtins

import pytest

from coder_agent.config import cfg
from coder_agent.core import agent as agent_module
from coder_agent.core.agent_errors import (
    build_error_guidance,
    classify_error,
    extract_combined_failure_text,
    extract_failure_excerpt,
)
from coder_agent.core.agent import (
    Agent,
    TERMINATION_LOOP_EXCEPTION,
    TERMINATION_MAX_STEPS,
    TERMINATION_MODEL_STOP,
    TERMINATION_VERIFICATION_PASSED,
    TERMINATION_RETRY_EXHAUSTED,
    TERMINATION_TOOL_EXCEPTION,
    TERMINATION_TOOL_NONZERO_EXIT,
    TERMINATION_VERIFICATION_FAILED,
    VerificationResult,
)
from coder_agent.tools.base import Tool
from coder_agent.tools.execute import execute_tools


@dataclass
class DummyTool(Tool):
    async def execute(self, **kwargs) -> str:
        return ""


class FakeClient:
    def __init__(self, responses=None, error: Exception | None = None):
        self.responses = responses or []
        self.error = error
        self.index = 0

    async def chat(self, **kwargs):
        if self.error is not None:
            raise self.error
        response = self.responses[self.index]
        self.index += 1
        on_token = kwargs.get("on_token")
        if on_token:
            for block in response.get("content", []):
                if block.get("type") == "text":
                    await on_token(block["text"])
        return response


def _tool_call_response() -> dict:
    return {
        "content": [{"type": "text", "text": "<think>run command</think>"}],
        "tool_uses": [{"id": "call_1", "name": "run_command", "input": {"command": "python solution.py"}}],
    }


def _unknown_tool_call_response() -> dict:
    return {
        "content": [{"type": "text", "text": "<think>call missing tool</think>"}],
        "tool_uses": [{"id": "call_1", "name": "missing_tool", "input": {"path": "demo.txt"}}],
    }


def _write_file_response(path: str) -> dict:
    return {
        "content": [{"type": "text", "text": f"<think>write {path}</think>"}],
        "tool_uses": [{
            "id": f"call_{path}",
            "name": "write_file",
            "input": {"path": path, "operation": "write", "content": "demo"},
        }],
    }


def _final_response(text: str = "done") -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "tool_uses": [],
    }


def _parse_error_response() -> dict:
    return {
        "content": [{"type": "text", "text": "<think>bad tool call</think>"}],
        "tool_uses": [],
        "parse_errors": ["run_command: malformed tool arguments (Expecting value at char 10). Raw: {bad"],
    }


class FakeMemory:
    def __init__(self, recent_tasks=None):
        self.recent_tasks = recent_tasks or []
        self.project_ids: list[str] = []
        self.recorded: list[tuple[str, str, object]] = []

    def get_or_create_project(self, workspace):
        self.project_ids.append(str(workspace))
        return "project-1"

    def get_recent_tasks(self, project_id, n=3):
        return self.recent_tasks[:n]

    def record_task(self, project_id, description, result):
        self.recorded.append((project_id, description, result))


class FakeTrajectoryStore:
    def __init__(self):
        self.started: list[dict] = []
        self.recorded_steps: list[tuple[str, object]] = []
        self.finished: list[dict] = []

    def start_trajectory(self, task_id, experiment_id, config, random_seed=42):
        self.started.append({
            "task_id": task_id,
            "experiment_id": experiment_id,
            "config": config,
            "random_seed": random_seed,
        })
        return "traj-1"

    def record_step(self, traj_id, step):
        self.recorded_steps.append((traj_id, step))

    def finish_trajectory(
        self,
        traj_id,
        final_status,
        termination_reason=None,
        partial_score=0.0,
        total_tokens=0,
        duration=None,
    ):
        self.finished.append({
            "traj_id": traj_id,
            "final_status": final_status,
            "termination_reason": termination_reason,
            "partial_score": partial_score,
            "total_tokens": total_tokens,
            "duration": duration,
        })


def _agent(
    client: FakeClient,
    experiment_config: dict | None = None,
    *,
    memory=None,
    trajectory_store=None,
) -> Agent:
    return Agent(
        tools=[
            DummyTool(
                name="run_command",
                description="Run command",
                input_schema={"type": "object"},
            )
        ],
        client=client,
        memory=memory,
        trajectory_store=trajectory_store,
        experiment_config=experiment_config or {},
    )


def test_safe_print_swallows_oserror(monkeypatch):
    calls = {"count": 0}

    def flaky_print(*args, **kwargs):
        calls["count"] += 1
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(builtins, "print", flaky_print)
    agent = _agent(FakeClient([]))

    agent._safe_print("stream chunk", end="")

    assert calls["count"] == 2


def test_build_import_error_guidance_prefers_local_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.agent, "workspace", tmp_path)
    package_dir = tmp_path / "utils"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")

    agent = _agent(FakeClient([]))
    guidance = agent._build_import_error_guidance(
        "Traceback...\nFile \"main.py\", line 1\nModuleNotFoundError: No module named 'utils'"
    )

    assert "project-local import" in guidance
    assert "utils/__init__.py" in guidance
    assert "before installing anything" in guidance


def test_build_import_error_guidance_handles_third_party_module(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.agent, "workspace", tmp_path)
    agent = _agent(FakeClient([]))

    guidance = agent._build_import_error_guidance(
        "Traceback...\nFile \"main.py\", line 1\nModuleNotFoundError: No module named 'requests'"
    )

    assert "Only try `pip install`" in guidance
    assert "requests" in guidance


def test_build_import_error_guidance_escalates_repeated_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.agent, "workspace", tmp_path)
    agent = _agent(FakeClient([]))

    guidance = agent._build_import_error_guidance(
        "Traceback...\nModuleNotFoundError: No module named 'requests'",
        repeated=True,
    )

    assert "repeated" in guidance
    assert "Do not repeat the previous fix blindly" in guidance


def test_classify_error_reads_assertion_failure_from_stdout():
    content = (
        "Exit code: 1\n"
        "STDOUT:\n"
        "FAILED test_demo.py::test_case - AssertionError: boom\n"
        "STDERR:\n"
    )

    failure_text = extract_combined_failure_text(content)

    assert "AssertionError: boom" in failure_text
    assert classify_error(failure_text) == "AssertionError"


def test_build_error_guidance_handles_pytest_collection_failures():
    guidance = build_error_guidance(
        "LogicError",
        "============================= test session starts =============================\n"
        "collecting ... collected 0 items / 1 error\n"
        "ERROR collecting test_markdown_table.py",
    )

    assert "collect" in guidance.lower()
    assert "syntax" in guidance.lower() or "import" in guidance.lower()


def test_classify_error_treats_api_signature_mismatch_as_logic_error():
    failure_text = (
        "TypeError: format_markdown_table() got an unexpected keyword argument 'headers'\n"
        "TypeError: format_markdown_table() takes 1 positional argument but 2 were given"
    )

    guidance = build_error_guidance("LogicError", failure_text)

    assert classify_error(failure_text) == "LogicError"
    assert "api" in guidance.lower() or "signature" in guidance.lower()


def test_extract_failure_excerpt_prefers_assertion_block():
    failure_text = (
        "============================= test session starts =============================\n"
        "FAILED test_markdown_table.py::test_basic_table - AssertionError\n"
        "    assert result == expected\n"
        "E       AssertionError: assert 'a' == 'b'\n"
        "E         - expected\n"
        "E         + actual\n"
    )

    excerpt = extract_failure_excerpt(failure_text)

    assert "AssertionError" in excerpt
    assert "expected" in excerpt


@pytest.mark.asyncio
async def test_exit_code_zero_with_stderr_does_not_trigger_failure(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\nwarning only",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    agent = _agent(FakeClient([_tool_call_response(), _final_response("done")]), {"correction": True})

    result = await agent._loop("task")

    assert result.success is True
    assert result.retry_steps == 0
    assert result.termination_reason == TERMINATION_MODEL_STOP


@pytest.mark.asyncio
async def test_nonzero_exit_without_correction_fails_with_tool_nonzero_exit(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 1\nSTDOUT:\n\nSTDERR:\nSyntaxError: invalid syntax",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    agent = _agent(FakeClient([_tool_call_response()]), {"correction": False})

    result = await agent._loop("task")

    assert result.success is False
    assert result.final_status == "failed"
    assert result.termination_reason == TERMINATION_TOOL_NONZERO_EXIT


@pytest.mark.asyncio
async def test_retry_exhausted_sets_termination_reason(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 1\nSTDOUT:\n\nSTDERR:\nAssertionError: boom",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    monkeypatch.setattr(cfg.agent, "max_retries", 0)
    agent = _agent(FakeClient([_tool_call_response()]), {"correction": True})

    result = await agent._loop("task")

    assert result.success is False
    assert result.termination_reason == TERMINATION_RETRY_EXHAUSTED


@pytest.mark.asyncio
async def test_recoverable_tool_error_enters_retry_path(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Error: old_text not found in file",
            "is_error": True,
            "error_kind": "tool_error",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    agent = _agent(FakeClient([_tool_call_response(), _final_response("done")]), {"correction": True})

    result = await agent._loop("task")

    assert result.success is True
    assert result.retry_steps == 1
    assert result.termination_reason == TERMINATION_MODEL_STOP


@pytest.mark.asyncio
async def test_retry_feedback_is_added_as_user_message(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": (
                "Exit code: 1\n"
                "STDOUT:\n"
                "TypeError: format_markdown_table() takes 1 positional argument but 2 were given\n"
                "STDERR:\n"
            ),
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    agent = _agent(FakeClient([_tool_call_response(), _final_response("done")]), {"correction": True})

    result = await agent._loop("task")

    assert result.success is True
    user_messages = [msg["content"] for msg in agent.history.messages if msg["role"] == "user"]
    assert any("Before the next write" in message for message in user_messages)
    assert any("stable" in message.lower() and "api" in message.lower() for message in user_messages)
    assert any("Focus on the first concrete failure" in message for message in user_messages)


@pytest.mark.asyncio
async def test_retry_policy_blocks_switching_files_before_rerun(monkeypatch):
    executed_paths: list[str] = []
    run_calls = {"count": 0}

    async def fake_execute_tools(tool_calls, tool_dict):
        first_tool = tool_calls[0]["name"]
        if first_tool == "run_command":
            run_calls["count"] += 1
            exit_code = 1 if run_calls["count"] == 1 else 0
            body = "AssertionError: boom" if exit_code == 1 else ""
            return [{
                "type": "tool_result",
                "tool_use_id": "call_run",
                "content": f"Exit code: {exit_code}\nSTDOUT:\n{body}\nSTDERR:\n",
            }]
        executed_paths.extend(call["input"].get("path", "") for call in tool_calls)
        return [{
            "type": "tool_result",
            "tool_use_id": tool_calls[0]["id"],
            "content": "Written",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    agent = _agent(
        FakeClient([
            _tool_call_response(),
            _write_file_response("markdown_table.py"),
            _write_file_response("test_markdown_table.py"),
            _tool_call_response(),
            _final_response("done"),
        ]),
        {"correction": True},
    )

    result = await agent._loop("task")

    assert result.success is True
    assert executed_paths == ["markdown_table.py"]
    user_messages = [msg["content"] for msg in agent.history.messages if msg["role"] == "user"]
    assert any("stay on one file only" in message for message in user_messages)


@pytest.mark.asyncio
async def test_unknown_tool_remains_hard_failure():
    agent = _agent(FakeClient([_unknown_tool_call_response()]), {"correction": True})

    result = await agent._loop("task")

    assert result.success is False
    assert result.termination_reason == TERMINATION_TOOL_EXCEPTION


@pytest.mark.asyncio
async def test_execute_tools_marks_error_string_as_tool_error():
    class ErrorTool(Tool):
        async def execute(self, **kwargs) -> str:
            return "Error: file not found: demo.txt"

    tool = ErrorTool(name="read_file", description="Read file", input_schema={"type": "object"})
    results = await execute_tools(
        [{"id": "call_1", "name": "read_file", "input": {"path": "demo.txt"}}],
        {"read_file": tool},
    )

    assert results[0]["is_error"] is True
    assert results[0]["error_kind"] == "tool_error"


@pytest.mark.asyncio
async def test_loop_exception_sets_termination_reason():
    agent = _agent(FakeClient(error=RuntimeError("client failed")))

    result = await agent._loop("task")

    assert result.success is False
    assert result.termination_reason == TERMINATION_LOOP_EXCEPTION
    assert "Exception class: RuntimeError" in result.content
    assert "Exception stage: llm.chat" in result.content


@pytest.mark.asyncio
async def test_max_steps_sets_termination_reason(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    monkeypatch.setattr(cfg.agent, "max_steps", 1)
    agent = _agent(FakeClient([_tool_call_response()]))

    result = await agent._loop("task")

    assert result.success is False
    assert result.final_status == "timeout"
    assert result.termination_reason == TERMINATION_MAX_STEPS


@pytest.mark.asyncio
async def test_verification_hook_passes_and_allows_model_stop(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    agent = _agent(FakeClient([_tool_call_response(), _final_response("done")]), {"correction": True})

    result = await agent._loop(
        "task",
        verification_hook=lambda: VerificationResult(True, "ok"),
    )

    assert result.success is True
    assert result.termination_reason == TERMINATION_MODEL_STOP


@pytest.mark.asyncio
async def test_auto_complete_on_verification_stops_after_successful_tool_batch(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    agent = _agent(FakeClient([_tool_call_response()]), {"correction": True})

    result = await agent._loop(
        "task",
        verification_hook=lambda: VerificationResult(True, "official pass"),
        auto_complete_on_verification=True,
    )

    assert result.success is True
    assert result.termination_reason == TERMINATION_VERIFICATION_PASSED
    assert result.content == "official pass"


@pytest.mark.asyncio
async def test_auto_complete_finalization_records_memory_once(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    memory = FakeMemory()
    trajectory_store = FakeTrajectoryStore()
    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    agent = _agent(
        FakeClient([_tool_call_response()]),
        {"correction": True},
        memory=memory,
        trajectory_store=trajectory_store,
    )

    result = await agent._loop(
        "task",
        verification_hook=lambda: VerificationResult(True, "official pass"),
        auto_complete_on_verification=True,
    )

    assert result.success is True
    assert len(memory.recorded) == 1
    assert memory.recorded[0][1] == "task"
    assert len(trajectory_store.finished) == 1
    assert trajectory_store.finished[0]["termination_reason"] == TERMINATION_VERIFICATION_PASSED


@pytest.mark.asyncio
async def test_auto_complete_on_verification_does_not_short_circuit_on_failed_check(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    client = FakeClient([_tool_call_response(), _final_response("done")])
    agent = _agent(client, {"correction": True})
    attempts = {"count": 0}

    def verification_hook():
        attempts["count"] += 1
        if attempts["count"] == 1:
            return VerificationResult(False, "not yet")
        return VerificationResult(True, "passed")

    result = await agent._loop(
        "task",
        verification_hook=verification_hook,
        auto_complete_on_verification=True,
    )

    assert result.success is True
    assert result.termination_reason == TERMINATION_MODEL_STOP
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_parse_errors_request_retry_instead_of_stopping(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    client = FakeClient([_parse_error_response(), _tool_call_response(), _final_response("done")])
    agent = _agent(client, {"correction": True})

    result = await agent._loop("task")

    assert result.success is True
    assert result.termination_reason == TERMINATION_MODEL_STOP
    assert client.index == 3


@pytest.mark.asyncio
async def test_verification_hook_failure_retries_then_succeeds(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    client = FakeClient([
        _tool_call_response(),
        _final_response("first answer"),
        _tool_call_response(),
        _final_response("second answer"),
    ])
    agent = _agent(client, {"correction": True})
    attempts = {"count": 0}

    def verification_hook():
        attempts["count"] += 1
        if attempts["count"] == 1:
            return VerificationResult(False, "benchmark failed")
        return VerificationResult(True, "passed")

    result = await agent._loop("task", verification_hook=verification_hook)

    assert result.success is True
    assert result.termination_reason == TERMINATION_MODEL_STOP
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_verification_hook_failure_exhausts_attempts(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    client = FakeClient([
        _tool_call_response(),
        _final_response("first answer"),
        _tool_call_response(),
        _final_response("second answer"),
    ])
    agent = _agent(client, {"correction": True})

    result = await agent._loop(
        "task",
        verification_hook=lambda: VerificationResult(False, "still failing"),
        max_verification_attempts=2,
    )

    assert result.success is False
    assert result.termination_reason == TERMINATION_VERIFICATION_FAILED
    assert "still failing" in result.content


@pytest.mark.asyncio
async def test_timeout_finalization_records_memory_once(monkeypatch):
    async def fake_execute_tools(tool_calls, tool_dict):
        return [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "Exit code: 0\nSTDOUT:\n\nSTDERR:\n",
        }]

    memory = FakeMemory()
    trajectory_store = FakeTrajectoryStore()
    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)
    monkeypatch.setattr(cfg.agent, "max_steps", 1)
    agent = _agent(
        FakeClient([_tool_call_response()]),
        {"correction": True},
        memory=memory,
        trajectory_store=trajectory_store,
    )

    result = await agent._loop("task")

    assert result.success is False
    assert result.termination_reason == TERMINATION_MAX_STEPS
    assert len(memory.recorded) == 1
    assert len(trajectory_store.finished) == 1
    assert trajectory_store.finished[0]["termination_reason"] == TERMINATION_MAX_STEPS
