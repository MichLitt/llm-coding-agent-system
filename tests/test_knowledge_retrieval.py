from __future__ import annotations

import json
import urllib.error

import pytest

from coder_agent.core.tool_registry import build_tools
from coder_agent.tools.knowledge_retrieval import KnowledgeRetrievalTool


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


@pytest.mark.asyncio
async def test_retrieval_formats_source_pages_and_clamps_top_k(monkeypatch):
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        return _Response(
            {
                "results": [
                    {
                        "doc_id": "doc-1",
                        "text": "The release process requires a gate.",
                        "score": 0.9,
                        "metadata": {
                            "source": "manual.pdf",
                            "page_start": 2,
                            "page_end": 3,
                        },
                    }
                ],
                "latency_ms": 4.2,
                "index_id": "docs",
                "retrieval_profile": "auto",
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    tool = KnowledgeRetrievalTool(base_url="http://rag.local/", timeout_seconds=2.5)

    result = await tool.execute(query=" release gate ", index_id="docs", top_k=99)

    assert captured == {
        "url": "http://rag.local/v1/retrieve",
        "body": {"query": "release gate", "index_id": "docs", "top_k": 20},
        "timeout": 2.5,
    }
    assert "manual.pdf [p.2–3]" in result
    assert "release process" in result
    assert "4.2ms" in result


@pytest.mark.asyncio
async def test_retrieval_requires_configuration(monkeypatch):
    monkeypatch.delenv("RAG_API_URL", raising=False)
    result = await KnowledgeRetrievalTool().execute(query="docs")
    assert result.startswith("Error: RAG_API_URL is not configured")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"query": ""}, "query must be"),
        ({"query": "ok", "index_id": ""}, "index_id must be"),
        ({"query": "ok", "top_k": 1.5}, "top_k must be"),
    ],
)
async def test_retrieval_validates_arguments(kwargs, message):
    result = await KnowledgeRetrievalTool(base_url="http://rag.local").execute(**kwargs)
    assert result.startswith("Error:")
    assert message in result


@pytest.mark.asyncio
async def test_retrieval_handles_http_error(monkeypatch):
    def fail(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "unavailable", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail)
    result = await KnowledgeRetrievalTool(base_url="http://rag.local").execute(query="x")
    assert result == "Error: knowledge retrieval service returned HTTP 503."


@pytest.mark.asyncio
async def test_retrieval_rejects_bad_json(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _Response(b"nope"))
    result = await KnowledgeRetrievalTool(base_url="http://rag.local").execute(query="x")
    assert result.startswith("Error: knowledge retrieval service returned invalid JSON")


@pytest.mark.asyncio
async def test_retrieval_handles_empty_results(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response({"results": [], "latency_ms": 1}),
    )
    result = await KnowledgeRetrievalTool(base_url="http://rag.local").execute(query="missing")
    assert result == "No results found for query: 'missing'"


def test_tool_registry_is_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_API_URL", raising=False)
    assert "knowledge_retrieval" not in {tool.name for tool in build_tools(tmp_path)}

    monkeypatch.setenv("RAG_API_URL", "http://rag.local")
    assert "knowledge_retrieval" in {tool.name for tool in build_tools(tmp_path)}
