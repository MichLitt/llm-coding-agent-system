"""Tests for MessageHistory.truncate() token alignment and compress_observation edge cases."""

import pytest

from coder_agent.core import Agent
from coder_agent.core.context import MessageHistory, compress_observation
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
async def test_message_tokens_parallel_after_mixed_adds():
    history = _make_history()
    await history.add_message("user", "hello")
    await history.add_message(
        "assistant",
        "hi",
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    await history.add_message("tool", "tool result", tool_calls=[{"id": "c1"}])
    await history.add_message(
        "assistant",
        "done",
        usage={"input_tokens": 20, "output_tokens": 8},
    )

    assert len(history.messages) == len(history.message_tokens)


@pytest.mark.asyncio
async def test_non_assistant_messages_get_zero_tokens():
    history = _make_history()
    await history.add_message("user", "question")
    await history.add_message("tool", "result", tool_calls=[{"id": "c1"}])

    assert history.message_tokens[0] == (0, 0)
    assert history.message_tokens[1] == (0, 0)


@pytest.mark.asyncio
async def test_assistant_message_gets_correct_tokens():
    history = _make_history()
    await history.add_message(
        "assistant",
        "answer",
        usage={"input_tokens": 30, "output_tokens": 15},
    )

    assert history.message_tokens[0] == (30, 15)
    assert history.total_tokens == len("system prompt") // 4 + 45


@pytest.mark.asyncio
async def test_truncate_keeps_lists_parallel():
    # Force context to be tiny so truncation fires
    history = _make_history(context_window_tokens=10)
    history.total_tokens = 200  # artificially high

    await history.add_message("user", "user message")
    await history.add_message(
        "assistant",
        "assistant message",
        usage={"input_tokens": 50, "output_tokens": 25},
    )

    history.truncate()

    assert len(history.messages) == len(history.message_tokens)


@pytest.mark.asyncio
async def test_truncate_does_not_subtract_assistant_tokens_for_user_message():
    """
    Regression: before the fix, popping a user message at index 0 would also
    pop message_tokens[0], which was the first assistant message's token count.
    After the fix, user messages carry (0, 0) so total_tokens only decreases by 0.
    """
    history = _make_history(context_window_tokens=10)

    await history.add_message("user", "question")
    await history.add_message(
        "assistant",
        "answer",
        usage={"input_tokens": 20, "output_tokens": 10},
    )
    # Force total_tokens above the window threshold
    history.total_tokens = 200

    # tokens attributed to the assistant message
    assistant_tokens = 30

    history.truncate()

    # After popping the user message (0 tokens), total_tokens should drop by 0.
    # After also popping the assistant message (30 tokens), it drops by 30.
    # In either case the lists must remain parallel.
    assert len(history.messages) == len(history.message_tokens)


@pytest.mark.asyncio
async def test_truncation_notice_keeps_lists_parallel():
    """After truncation notice is inserted, lists remain parallel."""
    history = _make_history(context_window_tokens=10)
    history.total_tokens = 200

    await history.add_message("user", "q1")
    await history.add_message("user", "q2")
    await history.add_message(
        "assistant",
        "a",
        usage={"input_tokens": 5, "output_tokens": 3},
    )

    history.truncate()

    assert len(history.messages) == len(history.message_tokens)
    # The truncation notice is a user message — check its token entry is (0,0)
    if history.messages and history.messages[0].get("content") == "[Earlier history has been truncated.]":
        assert history.message_tokens[0] == (0, 0)


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
