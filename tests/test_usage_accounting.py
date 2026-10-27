"""Provider-reported token usage: normalization, client accumulation, run attribution."""

import asyncio
import os
import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from coder_agent.config import LLMProfile, ModelPricing, resolve_llm_profile
from coder_agent.core.agent import Agent
from coder_agent.core.agent_types import ModelConfig
from coder_agent.core.llm_client import LLMClient, _AnthropicBackend, _OpenAIBackend
from coder_agent.core.usage import (
    LLMUsage,
    UsageTotals,
    usage_from_anthropic,
    usage_from_openai,
)
from coder_agent.memory.run_state import RunStateStore
from coder_agent.tools.base import Tool


# ---------------------------------------------------------------------------
# UsageTotals and normalizers
# ---------------------------------------------------------------------------

def test_usage_totals_status_and_totals():
    totals = UsageTotals()
    assert totals.status == "unavailable"
    assert totals.total_tokens is None

    totals.record(LLMUsage(input_tokens=100, output_tokens=20))
    assert totals.status == "provider"
    assert totals.total_tokens == 120

    totals.record(None)
    assert totals.status == "partial"
    assert totals.llm_calls == 2
    assert totals.reported_calls == 1
    assert totals.total_tokens == 120


def test_usage_report_uses_none_when_nothing_was_reported():
    totals = UsageTotals()
    totals.record(None)
    report = totals.to_report(ModelPricing(input_usd_per_mtok=1.0, output_usd_per_mtok=2.0))
    assert report == {
        "llm_calls": 1,
        "usage_reported_calls": 0,
        "token_usage_status": "unavailable",
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "total_tokens": None,
        "estimated_cost_usd": None,
    }


def test_estimated_cost_prices_cached_input_separately():
    totals = UsageTotals()
    totals.record(LLMUsage(input_tokens=1_000_000, output_tokens=500_000, cached_input_tokens=400_000))
    pricing = ModelPricing(input_usd_per_mtok=1.0, output_usd_per_mtok=4.0, cached_input_usd_per_mtok=0.1)
    # 600k uncached * $1 + 400k cached * $0.1 + 500k output * $4
    assert totals.estimated_cost_usd(pricing) == pytest.approx(0.6 + 0.04 + 2.0)
    assert totals.estimated_cost_usd(None) is None


def test_usage_totals_round_trip_and_arithmetic():
    first = UsageTotals(llm_calls=2, reported_calls=2, input_tokens=10, output_tokens=5)
    second = UsageTotals.from_state(first.to_state())
    assert second == first
    combined = first + UsageTotals(llm_calls=1, reported_calls=1, input_tokens=3, output_tokens=1)
    assert combined.total_tokens == 19
    assert (combined - first).total_tokens == 4
    assert UsageTotals.from_state(None) == UsageTotals()


def test_usage_from_openai_reads_cached_prompt_tokens():
    raw = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=30,
        prompt_tokens_details=SimpleNamespace(cached_tokens=100),
    )
    assert usage_from_openai(raw) == LLMUsage(input_tokens=120, output_tokens=30, cached_input_tokens=100)
    assert usage_from_openai({"prompt_tokens": 7, "completion_tokens": 3}) == LLMUsage(7, 3)
    assert usage_from_openai(None) is None
    assert usage_from_openai(MagicMock()) is None


def test_usage_from_anthropic_sums_cache_reads_and_writes_into_input():
    raw = SimpleNamespace(
        input_tokens=50,
        output_tokens=10,
        cache_creation_input_tokens=20,
        cache_read_input_tokens=30,
    )
    assert usage_from_anthropic(raw) == LLMUsage(input_tokens=100, output_tokens=10, cached_input_tokens=30)
    assert usage_from_anthropic(SimpleNamespace(input_tokens=5, output_tokens=1)) == LLMUsage(5, 1)


def test_resolve_llm_profile_parses_optional_pricing():
    config = {
        "llm": {
            "default_profile": "priced",
            "profiles": {
                "priced": {
                    "transport": "openai",
                    "model": "m",
                    "api_key_env": "LLM_PRICED_API_KEY",
                    "pricing": {"input_usd_per_mtok": 0.5, "output_usd_per_mtok": 2},
                },
                "unpriced": {"transport": "openai", "model": "m", "api_key_env": "LLM_PRICED_API_KEY"},
            },
        }
    }
    with patch("coder_agent.config._Y", config), patch.dict(os.environ, {"LLM_PRICED_API_KEY": "k"}):
        priced = resolve_llm_profile("priced")
        unpriced = resolve_llm_profile("unpriced")
    assert priced.pricing == ModelPricing(input_usd_per_mtok=0.5, output_usd_per_mtok=2.0)
    assert unpriced.pricing is None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _ChunkStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _text_chunk(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))],
        usage=None,
    )


def _openai_backend(fake_create, loop):
    backend = _OpenAIBackend.__new__(_OpenAIBackend)
    backend._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    backend._client_loop_id = id(loop)
    return backend


@pytest.mark.asyncio
async def test_openai_backend_requests_and_reads_stream_usage():
    requests = []

    async def fake_create(**kwargs):
        requests.append(kwargs)
        usage_chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=40, prompt_tokens_details=None),
        )
        return _ChunkStream([_text_chunk("hi"), usage_chunk])

    backend = _openai_backend(fake_create, asyncio.get_running_loop())
    response = await backend.chat(
        messages=[], system="s", tools=[], model="m", max_tokens=16, temperature=0.0
    )

    assert requests[0]["stream_options"] == {"include_usage": True}
    assert response["content"] == [{"type": "text", "text": "hi"}]
    assert response["usage"] == {"input_tokens": 200, "output_tokens": 40, "cached_input_tokens": 0}


@pytest.mark.asyncio
async def test_openai_backend_falls_back_when_provider_rejects_stream_options():
    requests = []
    rejected = openai.BadRequestError(
        "unknown parameter: stream_options",
        response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")),
        body=None,
    )

    async def fake_create(**kwargs):
        requests.append(dict(kwargs))
        if "stream_options" in kwargs:
            raise rejected
        return _ChunkStream([_text_chunk("ok")])

    backend = _openai_backend(fake_create, asyncio.get_running_loop())
    first = await backend.chat(messages=[], system="s", tools=[], model="m", max_tokens=16, temperature=0.0)
    second = await backend.chat(messages=[], system="s", tools=[], model="m", max_tokens=16, temperature=0.0)

    assert first["usage"] is None
    assert second["content"] == [{"type": "text", "text": "ok"}]
    # One rejected attempt, then the fallback is remembered for later calls.
    assert ["stream_options" in request for request in requests] == [True, False, False]


@pytest.mark.asyncio
async def test_openai_backend_propagates_bad_request_unrelated_to_usage():
    async def fake_create(**kwargs):
        raise openai.BadRequestError(
            "context too long",
            response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")),
            body=None,
        )

    backend = _openai_backend(fake_create, asyncio.get_running_loop())
    with pytest.raises(openai.BadRequestError):
        await backend.chat(messages=[], system="s", tools=[], model="m", max_tokens=16, temperature=0.0)
    assert getattr(backend, "_stream_include_usage", True) is True


@pytest.mark.asyncio
async def test_anthropic_backend_reads_final_message_usage():
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Done.")],
        usage=SimpleNamespace(input_tokens=80, output_tokens=12),
    )

    class FakeStream:
        @property
        def text_stream(self):
            async def _gen():
                yield "Done."
            return _gen()

        async def get_final_message(self):
            return final

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    backend = _AnthropicBackend.__new__(_AnthropicBackend)
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = FakeStream()
    backend._client = mock_client
    backend._client_loop_id = id(asyncio.get_running_loop())

    response = await backend.chat(messages=[], system="s", tools=[], model="m", max_tokens=16, temperature=0.0)
    assert response["usage"] == {"input_tokens": 80, "output_tokens": 12, "cached_input_tokens": 0}


# ---------------------------------------------------------------------------
# Client accumulation and run attribution
# ---------------------------------------------------------------------------

class _ScriptedBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        return self.responses.pop(0)

    async def aclose(self):
        return None


def _scripted_client(responses, pricing=None):
    profile = LLMProfile(
        name="fake", transport="openai", model="fake-model", api_key="k", base_url=None, pricing=pricing
    )
    client = LLMClient(profile=profile)
    client._backend = _ScriptedBackend(responses)
    return client


def _response(*, text="", tool_uses=None, usage=None):
    return {
        "content": [{"type": "text", "text": text}] if text else [],
        "tool_uses": tool_uses or [],
        "parse_errors": [],
        "usage": usage,
    }


@dataclass
class _ReadTool(Tool):
    async def execute(self, **kwargs):
        return "file contents"


def _agent(client, store):
    return Agent(
        tools=[_ReadTool(name="read_file", description="read", input_schema={"type": "object"})],
        client=client,
        model_config=ModelConfig(),
        run_state_store=store,
    )


def test_llm_client_accumulates_reported_and_unreported_calls():
    client = _scripted_client([
        _response(text="a", usage={"input_tokens": 10, "output_tokens": 2}),
        _response(text="b", usage=None),
    ])

    async def _two_calls():
        for _ in range(2):
            await client.chat(messages=[], system="s", tools=[], model="m", max_tokens=8, temperature=0.0)

    asyncio.run(_two_calls())
    assert client.usage == UsageTotals(llm_calls=2, reported_calls=1, input_tokens=10, output_tokens=2)


class _CapturingEvalOps:
    reports: list = []

    def submit(self, report):
        _CapturingEvalOps.reports.append(report)


def test_agent_run_attributes_provider_usage_to_result_store_steps_and_evalops(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    pricing = ModelPricing(input_usd_per_mtok=1.0, output_usd_per_mtok=2.0)
    client = _scripted_client(
        [
            _response(
                text="look first",
                tool_uses=[{"id": "call_1", "name": "read_file", "input": {"path": "a.py"}}],
                usage={"input_tokens": 1000, "output_tokens": 50},
            ),
            _response(text="done", usage={"input_tokens": 1200, "output_tokens": 30}),
        ],
        pricing=pricing,
    )
    # Usage from an earlier run on the same client must not leak into this run.
    client.usage.record(LLMUsage(input_tokens=999, output_tokens=999))

    _CapturingEvalOps.reports = []
    with patch("coder_agent.evalops.client.EvalOpsClient.from_env", return_value=_CapturingEvalOps()):
        result = _agent(client, store).run("fix it", run_id="run-usage", record_memory=False)

    assert result.success is True
    assert result.total_tokens == 2280
    assert result.usage["token_usage_status"] == "provider"
    assert result.usage["llm_calls"] == 2
    assert result.usage["input_tokens"] == 2200
    assert result.usage["output_tokens"] == 80
    assert result.usage["estimated_cost_usd"] == pytest.approx((2200 * 1.0 + 80 * 2.0) / 1_000_000)

    run = store.get_run("run-usage")
    assert run["total_tokens"] == 2280
    assert run["input_tokens"] == 2200
    assert run["output_tokens"] == 80
    assert run["llm_calls"] == 2
    assert run["token_usage_status"] == "provider"
    assert [step["step_tokens"] for step in store.list_steps("run-usage")] == [1050, 1230]
    assert store.list_steps("run-usage")[-1]["loop_state_json"]["usage"]["input_tokens"] == 2200

    [report] = _CapturingEvalOps.reports
    assert report.total_tokens == 2280
    assert report.input_tokens == 2200
    assert report.output_tokens == 80
    assert report.llm_call_count == 2
    assert report.token_usage_status == "provider"


def test_agent_run_reports_null_tokens_when_provider_reports_none(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    client = _scripted_client([_response(text="done", usage=None)])

    _CapturingEvalOps.reports = []
    with patch("coder_agent.evalops.client.EvalOpsClient.from_env", return_value=_CapturingEvalOps()):
        result = _agent(client, store).run("fix it", run_id="run-none", record_memory=False)

    assert result.total_tokens is None
    assert result.usage["token_usage_status"] == "unavailable"
    assert result.usage["estimated_cost_usd"] is None
    run = store.get_run("run-none")
    # runs.total_tokens is NOT NULL for legacy readers; the status disambiguates.
    assert run["total_tokens"] == 0
    assert run["input_tokens"] is None
    assert run["token_usage_status"] == "unavailable"
    assert _CapturingEvalOps.reports[0].total_tokens is None


def test_resumed_run_adds_usage_recorded_before_interruption(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("run-resume-usage", "demo task", "cli")
    store.start_run("run-resume-usage")
    store.record_step(
        "run-resume-usage",
        0,
        thought="inspect",
        observation="found",
        tool_call_count=1,
        had_error=False,
        step_tokens=600,
        step_duration_ms=5,
        loop_state={
            "steps": 1,
            "all_tool_calls": ["read_file"],
            "successful_tool_calls": 1,
            "usage": {"llm_calls": 1, "reported_calls": 1, "input_tokens": 500, "output_tokens": 100},
        },
    )
    client = _scripted_client([_response(text="done", usage={"input_tokens": 700, "output_tokens": 20})])

    result = _agent(client, store).run("", run_id="run-resume-usage", resume=True, record_memory=False)

    assert result.usage["llm_calls"] == 2
    assert result.total_tokens == 500 + 100 + 700 + 20
    assert store.get_run("run-resume-usage")["total_tokens"] == 1320


def test_run_state_store_adds_usage_columns_to_existing_database(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, task_id TEXT, experiment_id TEXT NOT NULL, preset TEXT,
            llm_profile TEXT, workspace_path TEXT, task_description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', started_at REAL, finished_at REAL,
            total_steps INTEGER NOT NULL DEFAULT 0, total_tool_calls INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0, tool_success_rate REAL, termination_reason TEXT,
            error_summary TEXT, git_commit TEXT, config_json TEXT, created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO runs (run_id, experiment_id, task_description, created_at, total_tokens) "
        "VALUES ('old', 'cli', 'legacy', 0, 968)"
    )
    conn.commit()
    conn.close()

    RunStateStore(db_path).close()
    store = RunStateStore(db_path)  # second open must be idempotent

    old = store.get_run("old")
    assert old["total_tokens"] == 968
    assert old["input_tokens"] is None
    assert old["token_usage_status"] is None
