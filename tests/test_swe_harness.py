"""v0.8.1 SWE-bench harness switches: error semantics, edit lock, prompt, step budget,
finish check, output cap and tool-call cap. All are off by default."""

from dataclasses import dataclass

import pytest

from coder_agent.config import cfg
from coder_agent.core import agent as agent_module
from coder_agent.core.agent import Agent
from coder_agent.core.agent_tool_batch import is_uninformative_exit
from coder_agent.core.agent_types import TERMINATION_MODEL_STOP, TERMINATION_RETRY_EXHAUSTED
from coder_agent.core.context import MessageHistory, validate_tool_call_sequence
from coder_agent.tools.base import Tool


@dataclass
class DummyTool(Tool):
    async def execute(self, **kwargs) -> str:
        return ""


class ScriptedClient:
    """Replies in order and records every request's messages."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    async def chat(self, **kwargs):
        self.sent.append([dict(message) for message in kwargs["messages"]])
        return self.responses.pop(0)


def _agent(client, runtime_config=None):
    return Agent(
        tools=[DummyTool(name=name, description=name, input_schema={"type": "object"})
               for name in ("run_command", "write_file", "patch_file")],
        client=client,
        experiment_config={"correction": True},
        runtime_config=runtime_config or {},
    )


def _calls(*uses):
    return {"content": [{"type": "text", "text": "step"}], "tool_uses": list(uses)}


def _run(index, command="grep -rn missing ."):
    return {"id": f"run_{index}", "name": "run_command", "input": {"command": command}}


def _write(index, path="src/fix.py"):
    return {"id": f"write_{index}", "name": "write_file", "input": {"path": path, "operation": "write", "content": "x"}}


def _final(text="done"):
    return {"content": [{"type": "text", "text": text}], "tool_uses": []}


def _fake_tools(monkeypatch, outputs=None, seen=None):
    """run_command returns the next scripted output; edits succeed."""
    outputs = list(outputs or [])

    async def fake_execute_tools(tool_calls, tool_dict):
        if seen is not None:
            seen.append([call["name"] for call in tool_calls])
        results = []
        for call in tool_calls:
            if call["name"] == "run_command":
                content = outputs.pop(0) if outputs else "Exit code: 0\nSTDOUT:\nok\nSTDERR:\n"
            elif call["name"] == "__budget_error__":
                content = call["input"]["message"]
            else:
                content = f"Written: {call['input']['path']}"
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": content,
                            "is_error": content.startswith("Error:")})
        return results

    monkeypatch.setattr(agent_module, "execute_tools", fake_execute_tools)


NO_MATCH = "Exit code: 1\nSTDOUT:\n\nSTDERR:\n"
SIGPIPE = "Exit code: 141\nSTDOUT:\nline 1\nline 2\nSTDERR:\n"
FAILURE = "Exit code: 1\nSTDOUT:\n\nSTDERR:\nTraceback (most recent call last):\nAssertionError: boom\n"


def test_uninformative_exits_are_silent_searches_and_broken_pipes():
    assert is_uninformative_exit(NO_MATCH, 1)
    assert is_uninformative_exit(SIGPIPE, 141)
    assert not is_uninformative_exit(FAILURE, 1)
    assert not is_uninformative_exit("Exit code: 2\nSTDOUT:\nusage: grep\nSTDERR:\n", 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(("signals", "reason"), [("exit_code", TERMINATION_RETRY_EXHAUSTED),
                                                 ("semantic", TERMINATION_MODEL_STOP)])
async def test_semantic_error_signals_do_not_count_searches_with_no_match(monkeypatch, signals, reason):
    monkeypatch.setattr(cfg.agent, "max_retries", 1)
    _fake_tools(monkeypatch, [NO_MATCH, SIGPIPE, NO_MATCH])
    client = ScriptedClient([_calls(_run(i)) for i in range(3)] + [_final()])

    result = await _agent(client, {"error_signals": signals})._loop("task")

    assert result.termination_reason == reason


@pytest.mark.asyncio
async def test_semantic_error_signals_still_count_real_failures(monkeypatch):
    monkeypatch.setattr(cfg.agent, "max_retries", 1)
    _fake_tools(monkeypatch, [FAILURE, FAILURE])
    client = ScriptedClient([_calls(_run(i, "python repro.py")) for i in range(2)] + [_final()])

    result = await _agent(client, {"error_signals": "semantic"})._loop("task")

    assert result.termination_reason == TERMINATION_RETRY_EXHAUSTED


@pytest.mark.asyncio
@pytest.mark.parametrize(("edit_lock", "expected"), [(True, ["src/a.py"]), (False, ["src/a.py", "src/b.py"])])
async def test_edit_lock_switch_decides_whether_a_second_file_may_be_edited(monkeypatch, edit_lock, expected):
    seen = []
    _fake_tools(monkeypatch, [FAILURE], seen)
    client = ScriptedClient([_calls(_run(0, "python repro.py")), _calls(_write(1, "src/a.py")),
                             _calls(_write(2, "src/b.py")), _calls(_run(3, "python repro.py")), _final()])

    await _agent(client, {"edit_lock": edit_lock})._loop("task")

    assert [name for batch in seen for name in batch if name == "write_file"] == ["write_file"] * len(expected)


def test_swebench_prompt_profile_replaces_the_from_scratch_rules():
    swebench = _agent(ScriptedClient([]), {"prompt_profile": "swebench"}).system
    default = _agent(ScriptedClient([])).system

    assert "fixing an issue in an existing repository" in swebench
    assert "install" in default and "Do not install packages" in swebench
    assert "Write tests only for behavior explicitly required" not in swebench
    assert "Maximum" not in swebench


@pytest.mark.parametrize("settings", [{"prompt_profile": "short"}, {"error_signals": "smart"},
                                      {"edit_lock": "no"}, {"max_observation_chars": -1},
                                      {"max_tool_calls_per_reply": "20"}])
def test_invalid_harness_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        _agent(ScriptedClient([]), settings)


@pytest.mark.asyncio
async def test_step_budget_notices_come_at_thirty_percent_left_and_four_left(monkeypatch):
    _fake_tools(monkeypatch)
    client = ScriptedClient([_calls(_run(i, "ls")) for i in range(9)] + [_final()])

    await _agent(client, {"step_budget_notices": True})._loop("task", max_steps=10)

    notices = [(index + 1, message["content"]) for index, request in enumerate(client.sent)
               for message in request[-1:] if str(message.get("content", "")).startswith("[Step budget]")]
    # 10 steps: 5 left at step 6, 4 left at step 7.
    assert [step for step, _ in notices] == [6, 7]
    assert "make the fix now" in notices[0][1] and "Verify your change once" in notices[1][1]
    for request in client.sent:
        validate_tool_call_sequence(request)


@pytest.mark.asyncio
async def test_no_step_budget_notices_by_default(monkeypatch):
    _fake_tools(monkeypatch)
    client = ScriptedClient([_calls(_run(i, "ls")) for i in range(9)] + [_final()])

    await _agent(client)._loop("task", max_steps=10)

    assert not any("[Step budget]" in str(m.get("content")) for request in client.sent for m in request)


@pytest.mark.asyncio
async def test_finish_check_asks_once_to_verify_an_unchecked_change(monkeypatch):
    _fake_tools(monkeypatch)
    client = ScriptedClient([_calls(_write(0)), _final("fixed"), _calls(_run(1, "python repro.py")), _final("fixed")])

    result = await _agent(client, {"finish_check": True})._loop("task")

    assert result.termination_reason == TERMINATION_MODEL_STOP
    assert "[Finish check] You changed src/fix.py" in client.sent[2][-1]["content"]
    assert len(client.sent) == 4
    validate_tool_call_sequence(client.sent[-1])


@pytest.mark.asyncio
async def test_finish_check_asks_once_when_nothing_was_changed(monkeypatch):
    _fake_tools(monkeypatch)
    client = ScriptedClient([_final("nothing to do"), _final("still nothing to do")])

    result = await _agent(client, {"finish_check": True})._loop("task")

    assert result.termination_reason == TERMINATION_MODEL_STOP
    assert "[Finish check] You have not changed any file" in client.sent[1][-1]["content"]
    assert len(client.sent) == 2


@pytest.mark.asyncio
async def test_finish_check_stays_quiet_after_a_verified_change(monkeypatch):
    _fake_tools(monkeypatch)
    client = ScriptedClient([_calls(_write(0), _run(1, "python repro.py")), _final("fixed")])

    await _agent(client, {"finish_check": True})._loop("task")

    assert len(client.sent) == 2


@pytest.mark.asyncio
async def test_long_observations_are_cut_to_head_and_tail():
    history = MessageHistory(model="m", system="s", context_window_tokens=100_000, client=None,
                             experiment_config={"max_observation_chars": 100})
    await history.add_message("tool", "A" * 300 + "B" * 300, tool_calls=[{"id": "c1"}])

    content = history.messages[0]["content"]
    assert content.startswith("A" * 50) and content.endswith("B" * 50)
    assert "500 characters cut" in content and len(content) < 300


@pytest.mark.asyncio
async def test_calls_past_the_per_reply_cap_are_answered_without_running(monkeypatch):
    seen = []
    _fake_tools(monkeypatch, seen=seen)
    client = ScriptedClient([_calls(_run(0, "ls"), _run(1, "ls"), _run(2, "ls")), _final()])

    await _agent(client, {"max_tool_calls_per_reply": 2})._loop("task")

    assert seen == [["run_command", "run_command", "__budget_error__"]]
    validate_tool_call_sequence(client.sent[-1])
    assert any("at most 2 tool calls" in str(m.get("content")) for m in client.sent[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("hints", [True, False])
async def test_correction_hints_switch_keeps_the_run_going_without_hints(monkeypatch, hints):
    _fake_tools(monkeypatch, [FAILURE])
    client = ScriptedClient([_calls(_run(0, "python repro.py")), _final()])

    result = await _agent(client, {"correction_hints": hints})._loop("task")

    assert result.termination_reason == TERMINATION_MODEL_STOP
    # The hint reaches the model as a user message after the tool result.
    texts = [str(m.get("content")) for m in client.sent[-1] if m.get("role") == "user"]
    assert any(t.startswith("Previous command failed") for t in texts) is hints


@pytest.mark.asyncio
@pytest.mark.parametrize(("policy", "reason"), [("cumulative", TERMINATION_RETRY_EXHAUSTED), ("off", TERMINATION_MODEL_STOP)])
async def test_retry_policy_off_never_stops_a_run_for_failures(monkeypatch, policy, reason):
    monkeypatch.setattr(cfg.agent, "max_retries", 1)
    _fake_tools(monkeypatch, [FAILURE] * 4)
    client = ScriptedClient([_calls(_run(i, f"python repro.py --case {i}")) for i in range(4)] + [_final()])

    result = await _agent(client, {"retry_policy": policy})._loop("task")

    assert result.termination_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(("threshold", "warned"), [(2, True), (0, False)])
async def test_doom_loop_threshold_zero_turns_the_warning_off(monkeypatch, threshold, warned):
    _fake_tools(monkeypatch, [FAILURE, FAILURE])
    client = ScriptedClient([_calls(_run(0, "python repro.py")), _calls(_run(1, "python repro.py")), _final()])

    await _agent(client, {"doom_loop_threshold": threshold})._loop("task")

    assert any("same failing command" in str(m.get("content")) for m in client.sent[-1]) is warned


@pytest.mark.parametrize("settings", [{"correction_hints": "no"}, {"doom_loop_threshold": -1},
                                      {"retry_policy": "never"}])
def test_invalid_ablation_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        _agent(ScriptedClient([]), settings)
