"""Tool: search a RAG knowledge base via the retrieval API.

Reads RAG_API_URL from the environment (e.g. http://localhost:8080).
When RAG_API_URL is not set the tool returns a clear error string and
never raises, so the agent loop is not disrupted.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from coder_agent.tools.base import Tool


def _read_response(req: urllib.request.Request, timeout_seconds: float) -> str:
    """Perform the blocking urllib call outside the Agent event loop."""
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        return resp.read().decode("utf-8")


class KnowledgeRetrievalTool(Tool):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._timeout_seconds = timeout_seconds
        super().__init__(
            name="knowledge_retrieval",
            description=(
                "Search a knowledge base or document index for relevant passages. "
                "Use this when you need to look up information from indexed documents, "
                "technical manuals, or any pre-built knowledge index. "
                "Returns the top matching passages with source file and page metadata."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query.",
                    },
                    "index_id": {
                        "type": "string",
                        "description": "Name of the knowledge index to search.",
                        "default": "default",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (1–20).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )

    async def execute(
        self,
        query: str,
        index_id: str = "default",
        top_k: int = 5,
        **_: Any,
    ) -> str:
        if not isinstance(query, str) or not query.strip():
            return "Error: query must be a non-empty string."
        if not isinstance(index_id, str) or not index_id.strip():
            return "Error: index_id must be a non-empty string."
        if not isinstance(top_k, int) or isinstance(top_k, bool):
            return "Error: top_k must be an integer."

        base_url = self._base_url or os.environ.get("RAG_API_URL", "").rstrip("/")
        if not base_url:
            return (
                "Error: RAG_API_URL is not configured. "
                "Set RAG_API_URL=http://<host>:<port> to enable knowledge retrieval."
            )

        try:
            requested_top_k = max(1, min(top_k, 20))
            payload = json.dumps(
                {
                    "query": query.strip(),
                    "index_id": index_id.strip(),
                    "top_k": requested_top_k,
                }
            ).encode()
            req = urllib.request.Request(
                f"{base_url}/v1/retrieve",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            raw_response = await asyncio.to_thread(
                _read_response,
                req,
                self._timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            return f"Error: knowledge retrieval service returned HTTP {exc.code}."
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return f"Error: knowledge retrieval request failed: {exc}"

        try:
            data = json.loads(raw_response)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return f"Error: knowledge retrieval service returned invalid JSON: {exc}"
        if not isinstance(data, dict):
            return "Error: knowledge retrieval service returned a non-object response."

        results = data.get("results", [])
        if not isinstance(results, list):
            return "Error: knowledge retrieval response field 'results' must be a list."
        if not results:
            return f"No results found for query: {query!r}"

        latency = data.get("latency_ms", "?")
        lines: list[str] = [
            f"Retrieved {len(results)} result(s) from index '{index_id}' "
            f"(latency: {latency}ms):\n"
        ]
        for i, r in enumerate(results, 1):
            if not isinstance(r, dict):
                return "Error: knowledge retrieval response contains a malformed result."
            meta = r.get("metadata", {})
            if not isinstance(meta, dict):
                meta = {}
            source = meta.get("source") or ""
            page_start = meta.get("page_start")
            page_end = meta.get("page_end")
            page_info = ""
            if page_start is not None:
                page_info = f" [p.{page_start}"
                if page_end is not None and page_end != page_start:
                    page_info += f"–{page_end}"
                page_info += "]"
            header = f"[{i}]"
            if source:
                header += f" {source}{page_info}"
            elif page_info:
                header += page_info
            lines.append(header)
            result_text = r.get("text", "")
            if not isinstance(result_text, str):
                result_text = str(result_text)
            text = result_text.strip()
            lines.append(text[:500] + ("…" if len(text) > 500 else ""))
            lines.append("")

        return "\n".join(lines)
