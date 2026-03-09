from dataclasses import dataclass
import builtins

import pytest

from coder_agent.config import cfg
from coder_agent.core import agent as agent_module
from coder_agent.core.agent import (
    Agent,
    TERMINATION_LOOP_EXCEPTION,
    TERMINATION_MAX_STEPS,
    TERMINATION_MODEL_STOP,
    TERMINATION_RETRY_EXHAUSTED,
    TERMINATION_TOOL_NONZERO_EXIT,
    TERMINATION_VERIFICATION_FAILED,
    VerificationResult,
)
from coder_agent.tools.base import Tool


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


def _final_response(text: str = "done") -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "tool_uses": [],
    }


def _agent(client: FakeClient, experiment_config: dict | None = None) -> Agent:
    return Agent(
        tools=[
            DummyTool(
                name="run_command",
                description="Run command",
                input_schema={"type": "object"},
            )
        ],
        client=client,
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
