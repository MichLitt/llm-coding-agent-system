"""Tests for MessageHistory.truncate() token alignment and compress_observation edge cases."""

import pytest

from coder_agent.core import Agent
from coder_agent.core.context import (
    HistoryProtocolError,
    MessageHistory,
    compress_observation,
    estimate_tokens,
    validate_tool_call_sequence,
)
from coder_agent.tools.base import Tool


# ---------------------------------------------------------------------------
# compress_observation edge cases
# ---------------------------------------------------------------------------

def test_compress_observation_short_content_not_compressed():
    content = "line1\nline2\nline3"
    result = compress_observation(content)
    assert result.was_compressed is False
    assert result.content == content
    assert result.ratio == 1.0


def test_compress_observation_long_terminal_output_truncates_head():
    # 45 lines of terminal output (> _TERMINAL_THRESHOLD=40)
    lines = ["Exit code: 0"] + [f"line {i}" for i in range(44)]
    content = "\n".join(lines)
    result = compress_observation(content, experiment_config={"observation_compression_mode": "rule_based"})
    assert result.was_compressed is True
    assert "omitted" in result.content
    # tail lines are preserved
    assert "line 43" in result.content


def test_compress_observation_long_file_content_keeps_head_and_tail():
    # 35 lines of file content (> _FILE_CONTENT_THRESHOLD=30)
    lines = [f"code line {i}" for i in range(35)]
    content = "\n".join(lines)
    result = compress_observation(content, experiment_config={"observation_compression_mode": "rule_based"})
    assert result.was_compressed is True
    assert "omitted" in result.content
    # head lines preserved
    assert "code line 0" in result.content
    # tail lines preserved
    assert "code line 34" in result.content


def test_compress_observation_exactly_at_short_threshold_not_compressed():
    # Exactly _SHORT_THRESHOLD=10 lines — should NOT compress
    content = "\n".join(f"line {i}" for i in range(10))
    result = compress_observation(content)
    assert result.was_compressed is False


def test_compress_observation_empty_string():
    result = compress_observation("")
    assert result.was_compressed is False
    assert result.content == ""
    assert result.ratio == 1.0


def test_compress_observation_single_line():
    content = "just one line"
    result = compress_observation(content)
    assert result.was_compressed is False
    assert result.content == content


# ---------------------------------------------------------------------------
# MessageHistory.message_tokens parallel alignment
# ---------------------------------------------------------------------------

def _make_history(context_window_tokens: int = 100_000) -> MessageHistory:
    return MessageHistory(
        model="test-model",
        system="system prompt",
        context_window_tokens=context_window_tokens,
        client=None,
    )


class _SummaryClient:
    async def chat(self, **kwargs):
        return type(
            "SummaryResponse",
            (),
            {"content": [type("Block", (), {"text": '{"current_state": "ok"}'})()]},
        )()


@pytest.mark.asyncio
async def test_every_message_carries_its_estimated_size():
    history = _make_history()
    await history.add_message("user", "x" * 30)
    await history.add_message("assistant", "y" * 9, tool_calls=[{"id": "c1", "type": "function"}])
    await history.add_message("tool", "z" * 60, tool_calls=[{"id": "c1"}])

    assert len(history.messages) == len(history.message_tokens)
    assert history.message_tokens[0] == (10, 0)
    assert history.message_tokens[1][0] > 3  # the tool calls count too
    assert history.message_tokens[2] == (20, 0)
    assert history.total_tokens == estimate_tokens("system prompt") + sum(t for t, _ in history.message_tokens)


async def _task_with_turns(history, turns):
    await history.add_message("user", "the task " * 10)
    for index in range(turns):
        call = {"id": f"c{index}", "type": "function", "function": {"name": "run_command", "arguments": "{}"}}
        await history.add_message("assistant", "", tool_calls=[call])
        await history.add_message("tool", "output " * 50, tool_calls=[{"id": f"c{index}"}])
        await history.add_message("user", f"feedback {index}")


@pytest.mark.asyncio
async def test_truncate_drops_whole_tool_call_turns_and_keeps_the_task():
    history = _make_history(context_window_tokens=400)
    await _task_with_turns(history, 6)

    history.truncate()

    assert history.total_tokens <= 400
    assert history.messages[0]["content"].startswith("the task")
    assert history.messages[1] == {"role": "user", "content": "[Earlier history has been truncated.]"}
    validate_tool_call_sequence(history.messages)
    assert history.messages[-1]["content"] == "feedback 5"
    assert len(history.messages) == len(history.message_tokens)
    assert history.total_tokens == estimate_tokens("system prompt") + sum(t for t, _ in history.message_tokens)


@pytest.mark.asyncio
async def test_truncate_keeps_the_latest_turn_even_over_the_window():
    history = _make_history(context_window_tokens=10)
    await _task_with_turns(history, 1)

    history.truncate()

    assert [m["role"] for m in history.messages] == ["user", "assistant", "tool", "user"]


@pytest.mark.asyncio
async def test_truncate_does_nothing_within_the_window():
    history = _make_history()
    await _task_with_turns(history, 3)
    before = list(history.messages)

    history.truncate()

    assert history.messages == before


def test_tool_call_sequence_validation():
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}, {"id": "b"}]}
    validate_tool_call_sequence([{"role": "user", "content": "task"}, call,
                                 {"role": "tool", "tool_call_id": "b"}, {"role": "tool", "tool_call_id": "a"}])
    with pytest.raises(HistoryProtocolError, match="followed by results"):
        validate_tool_call_sequence([call, {"role": "user", "content": "warning"},
                                     {"role": "tool", "tool_call_id": "a"}, {"role": "tool", "tool_call_id": "b"}])
    with pytest.raises(HistoryProtocolError, match="followed by results"):
        validate_tool_call_sequence([call, {"role": "tool", "tool_call_id": "a"}])
    with pytest.raises(HistoryProtocolError, match="does not follow"):
        validate_tool_call_sequence([{"role": "user", "content": "task"}, {"role": "tool", "tool_call_id": "a"}])


@pytest.mark.asyncio
async def test_add_message_uses_runtime_observation_compression_override(monkeypatch):
    monkeypatch.setattr("coder_agent.core.context.cfg.context.observation_compression_mode", "rule_based")
    history = MessageHistory(
        model="test-model",
        system="system prompt",
        context_window_tokens=100_000,
        client=None,
        experiment_config={"observation_compression_mode": "smart"},
    )
    content = "\n".join(
        [
            "Exit code: 1",
            *[f"test_ok_{i} PASSED" for i in range(12)],
            "FAILED tests/test_demo.py::test_example - AssertionError: expected 1 == 2",
            *[f"context line {i}" for i in range(5)],
            "1 failed, 12 passed in 0.20s",
        ]
    )

    await history.add_message("tool", content, tool_calls=[{"id": "c1"}])

    stored = history.messages[0]["content"]
    assert "[12 passing tests omitted]" in stored
    assert "FAILED tests/test_demo.py::test_example" in stored
    assert "1 failed, 12 passed in 0.20s" in stored


@pytest.mark.asyncio
async def test_compact_moves_split_left_to_keep_tool_call_and_result_together():
    history = MessageHistory(
        model="test-model",
        system="system prompt",
        context_window_tokens=100_000,
        client=_SummaryClient(),
    )
    history.messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "analysis"},
        {"role": "user", "content": "more context"},
        {"role": "assistant", "content": "calling tool", "tool_calls": [{"id": "call_1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "tool output"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "next"},
    ]
    history.message_tokens = [(0, 0)] * len(history.messages)

    await history.compact(history.client, {"model": "test-model"}, keep_recent=3)

    assert history.messages[0]["content"].startswith("[Context compacted")
    assert history.messages[1]["role"] == "assistant"
    assert history.messages[1]["tool_calls"][0]["id"] == "call_1"
    assert history.messages[2]["role"] == "tool"


@pytest.mark.asyncio
async def test_compact_skips_when_only_unsafe_boundary_exists():
    history = MessageHistory(
        model="test-model",
        system="system prompt",
        context_window_tokens=100_000,
        client=_SummaryClient(),
    )
    history.messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "calling tool", "tool_calls": [{"id": "call_1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "tool output"},
    ]
    history.message_tokens = [(0, 0)] * len(history.messages)

    await history.compact(history.client, {"model": "test-model"}, keep_recent=1)

    assert history.messages[0]["role"] == "user"
    assert len(history.messages) == 3


class _DummyTool(Tool):
    async def execute(self, **kwargs):
        return {"content": "ok"}


def test_agent_reset_preserves_runtime_context_overrides():
    agent = Agent(
        tools=[_DummyTool(name="dummy", description="dummy", input_schema={"type": "object"})],
        client=None,
        runtime_config={"observation_compression_mode": "smart"},
    )

    agent.reset()

    assert agent.history.experiment_config["observation_compression_mode"] == "smart"
