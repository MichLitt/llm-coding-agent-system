"""LLM backend client using OpenAI-compatible API."""

import json
from collections import defaultdict
from typing import Any

from openai import AsyncOpenAI

from coder_agent.config import cfg


class LLMClient:
    """Async OpenAI-compatible client.

    Normalises responses to:
        {
            "content": [{"type": "text", "text": "..."}],
            "tool_uses": [{"id": "...", "name": "...", "input": {...}}],
        }
    """

    def __init__(self):
        self._client = AsyncOpenAI(
            api_key=cfg.model.api_key,
            base_url=cfg.model.base_url,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
        on_token: Any | None = None,
    ) -> dict[str, Any]:
        full_messages = [{"role": "system", "content": system}] + messages

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ] if tools else None

        text_chunks: list[str] = []
        tc_accum: dict[int, dict[str, str]] = defaultdict(lambda: {"id": "", "name": "", "arguments": ""})

        stream = await self._client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=openai_tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if delta.content:
                text_chunks.append(delta.content)
                if on_token:
                    await on_token(delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if tc.id:
                        tc_accum[idx]["id"] = tc.id
                    if tc.function.name:
                        tc_accum[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tc_accum[idx]["arguments"] += tc.function.arguments

        tool_uses = [
            {"id": v["id"], "name": v["name"], "input": json.loads(v["arguments"] or "{}")}
            for v in tc_accum.values()
        ]

        full_text = "".join(text_chunks)
        content = [{"type": "text", "text": full_text}] if full_text else []

        return {"content": content, "tool_uses": tool_uses}
