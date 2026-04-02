from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import asyncio

import pytest

from coder_agent.core.llm_client import (
    LLMClient,
    _AnthropicBackend,
    _OpenAIBackend,
    _extract_balanced_json_object,
    _normalize_messages_for_anthropic,
    _parse_tool_arguments,
)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def test_extract_balanced_json_object_handles_noise():
    raw = 'prefix {"command": "pytest -q", "timeout": 30} trailing'
    assert _extract_balanced_json_object(raw) == '{"command": "pytest -q", "timeout": 30}'


def test_parse_tool_arguments_accepts_complete_json():
    parsed, error = _parse_tool_arguments('{"command": "pytest -q"}', "run_command")
    assert error is None
    assert parsed == {"command": "pytest -q"}


def test_parse_tool_arguments_recovers_noisy_json():
    parsed, error = _parse_tool_arguments(
        'noise before {"command": "pytest -q", "timeout": 30} noise after',
        "run_command",
    )
    assert error is None
    assert parsed == {"command": "pytest -q", "timeout": 30}


# ---------------------------------------------------------------------------
# _normalize_messages_for_anthropic
# ---------------------------------------------------------------------------

def test_normalize_messages_strips_error_kind():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "error output",
                    "is_error": True,
                    "error_kind": "LogicError",
                }
            ],
        }
    ]
    result = _normalize_messages_for_anthropic(messages)
    block = result[0]["content"][0]
    assert "error_kind" not in block
    assert block["is_error"] is True
    assert block["content"] == "error output"


def test_normalize_messages_omits_is_error_when_false():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "ok",
                    "is_error": False,
                    "error_kind": "",
                }
            ],
        }
    ]
    result = _normalize_messages_for_anthropic(messages)
    block = result[0]["content"][0]
    assert "error_kind" not in block
    assert "is_error" not in block


def test_normalize_messages_preserves_non_tool_result_blocks():
    messages = [
        {"role": "user", "content": "plain text"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    result = _normalize_messages_for_anthropic(messages)
    assert result[0]["content"] == "plain text"
    assert result[1]["content"] == [{"type": "text", "text": "hi"}]


# ---------------------------------------------------------------------------
# LLMClient backend selection
# ---------------------------------------------------------------------------

def test_llm_client_builds_anthropic_backend_when_format_is_anthropic():
    with patch("coder_agent.core.llm_client.cfg") as mock_cfg:
        mock_cfg.model.api_format = "anthropic"
        mock_cfg.model.anthropic_api_key = "key"
        mock_cfg.model.anthropic_base_url = "https://api.minimax.io/anthropic"
        client = LLMClient()
    assert isinstance(client._backend, _AnthropicBackend)


def test_llm_client_builds_openai_backend_when_format_is_openai():
    with patch("coder_agent.core.llm_client.cfg") as mock_cfg:
        mock_cfg.model.api_format = "openai"
        mock_cfg.model.api_key = "key"
        mock_cfg.model.base_url = "https://example.com"
        client = LLMClient()
    assert isinstance(client._backend, _OpenAIBackend)


# ---------------------------------------------------------------------------
# _OpenAIBackend — invalid tool arguments → parse_errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_backend_chat_returns_parse_errors_for_invalid_tool_arguments():
    async def fake_create(**kwargs):
        chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    function=SimpleNamespace(
                                        name="run_command",
                                        arguments='{"command": ',
                                    ),
                                )
                            ],
                        )
                    )
                ]
            )
        ]

        class FakeStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not chunks:
                    raise StopAsyncIteration
                return chunks.pop(0)

        return FakeStream()

    backend = _OpenAIBackend.__new__(_OpenAIBackend)
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    backend._client_loop_id = id(asyncio.get_running_loop())

    response = await backend.chat(
        messages=[],
        system="system",
        tools=[{"name": "run_command", "description": "Run command", "input_schema": {"type": "object"}}],
        model="test-model",
        max_tokens=128,
        temperature=0.0,
    )

    assert response["tool_uses"] == []
    assert response["parse_errors"]
    assert "malformed tool arguments" in response["parse_errors"][0]


# ---------------------------------------------------------------------------
# _AnthropicBackend — parses text and tool_use blocks from final message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_anthropic_backend_chat_parses_text_and_tool_use_blocks():
    fake_text_block = SimpleNamespace(type="text", text="Hello!")
    fake_tool_block = SimpleNamespace(
        type="tool_use", id="call_1", name="run_command", input={"command": "pytest"}
    )
    fake_message = SimpleNamespace(content=[fake_text_block, fake_tool_block])

    class FakeStream:
        @property
        def text_stream(self):
            async def _gen():
                yield "Hello!"
            return _gen()

        async def get_final_message(self):
            return fake_message

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    backend = _AnthropicBackend.__new__(_AnthropicBackend)
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = FakeStream()
    backend._client = mock_client
    backend._client_loop_id = id(asyncio.get_running_loop())

    result = await backend.chat(
        messages=[],
        system="system",
        tools=[{"name": "run_command", "description": "Run", "input_schema": {"type": "object"}}],
        model="MiniMax-M2.7",
        max_tokens=128,
        temperature=0.0,
    )

    assert result["content"] == [{"type": "text", "text": "Hello!"}]
    assert result["tool_uses"] == [{"id": "call_1", "name": "run_command", "input": {"command": "pytest"}}]
    assert result["parse_errors"] == []


@pytest.mark.asyncio
async def test_anthropic_backend_chat_text_only_response():
    fake_message = SimpleNamespace(content=[SimpleNamespace(type="text", text="Done.")])

    class FakeStream:
        @property
        def text_stream(self):
            async def _gen():
                yield "Done."
            return _gen()

        async def get_final_message(self):
            return fake_message

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    backend = _AnthropicBackend.__new__(_AnthropicBackend)
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = FakeStream()
    backend._client = mock_client
    backend._client_loop_id = id(asyncio.get_running_loop())

    result = await backend.chat(
        messages=[], system="s", tools=[], model="m", max_tokens=128, temperature=0.0
    )

    assert result["content"] == [{"type": "text", "text": "Done."}]
    assert result["tool_uses"] == []
    assert result["parse_errors"] == []


# ---------------------------------------------------------------------------
# LLMClient close idempotency (via _OpenAIBackend)
# ---------------------------------------------------------------------------

class _FakeAsyncClient:
    def __init__(self):
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1


def test_llm_client_close_is_idempotent():
    client = LLMClient.__new__(LLMClient)
    backend = _OpenAIBackend.__new__(_OpenAIBackend)
    fake = _FakeAsyncClient()
    backend._client = fake
    backend._client_loop_id = 1
    client._backend = backend

    client.close()
    client.close()

    assert fake.close_calls == 1


@pytest.mark.asyncio
async def test_llm_client_aclose_is_idempotent():
    client = LLMClient.__new__(LLMClient)
    backend = _OpenAIBackend.__new__(_OpenAIBackend)
    fake = _FakeAsyncClient()
    backend._client = fake
    backend._client_loop_id = 1
    client._backend = backend

    await client.aclose()
    await client.aclose()

    assert fake.close_calls == 1
