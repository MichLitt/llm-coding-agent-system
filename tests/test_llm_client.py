from types import SimpleNamespace

import pytest

from coder_agent.core.llm_client import (
    LLMClient,
    _extract_balanced_json_object,
    _parse_tool_arguments,
)


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


@pytest.mark.asyncio
async def test_llm_client_chat_returns_parse_errors_for_invalid_tool_arguments():
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

    client = LLMClient.__new__(LLMClient)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )

    response = await client.chat(
        messages=[],
        system="system",
        tools=[{
            "name": "run_command",
            "description": "Run command",
            "input_schema": {"type": "object"},
        }],
        model="test-model",
        max_tokens=128,
        temperature=0.0,
    )

    assert response["tool_uses"] == []
    assert response["parse_errors"]
    assert "malformed tool arguments" in response["parse_errors"][0]
