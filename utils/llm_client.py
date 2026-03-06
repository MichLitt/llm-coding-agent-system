"""LLM backend client using OpenAI-compatible API.

Wraps the openai SDK pointed at a custom base_url so any OpenAI-compatible
provider (MiniMax, DeepSeek, Ollama, etc.) can be swapped in via .env.

Environment variables
---------------------
LLM_API_KEY     API key for the provider
LLM_BASE_URL    Base URL of the OpenAI-compatible endpoint

Usage
-----
    client = LLMClient()
    response = await client.chat(
        messages=[...],
        system="You are ...",
        tools=[...],
        model="MiniMax-M2.5",
        max_tokens=8192,
        temperature=1.0,
    )
    # response = {"content": [...], "tool_uses": [...]}
"""

import json
import os
from collections import defaultdict
from typing import Any

from openai import AsyncOpenAI


class LLMClient:
    """Async OpenAI-compatible client.

    Translates between the agent's internal format and the OpenAI API,
    then normalises the response back into a provider-agnostic dict:

        {
            "content": [{"type": "text", "text": "..."}],
            "tool_uses": [{"id": "...", "name": "...", "input": {...}}],
        }
    """

    def __init__(self):
        self._client = AsyncOpenAI(
            api_key=os.environ.get("LLM_API_KEY", ""),
            base_url=os.environ.get("LLM_BASE_URL", ""),
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
        """Call the LLM with streaming and return a normalised response dict.

        Parameters
        ----------
        messages   : conversation history from MessageHistory.format_for_api()
        system     : system prompt string
        tools      : list of tool dicts from Tool.to_dict()
        model      : model ID string
        max_tokens : max tokens for the response
        temperature: sampling temperature
        on_token   : optional async callable(token: str) called for each text chunk

        Returns
        -------
        dict with keys:
            "content"   : list of text blocks (may be empty)
            "tool_uses" : list of tool call dicts (may be empty)
        """
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
        # tool_call_id -> {"id", "name", "arguments"}
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

            # Stream text tokens
            if delta.content:
                text_chunks.append(delta.content)
                if on_token:
                    await on_token(delta.content)

            # Accumulate tool call fragments
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if tc.id:
                        tc_accum[idx]["id"] = tc.id
                    if tc.function.name:
                        tc_accum[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tc_accum[idx]["arguments"] += tc.function.arguments

        # Normalise tool calls
        tool_uses = [
            {"id": v["id"], "name": v["name"], "input": json.loads(v["arguments"] or "{}")}
            for v in tc_accum.values()
        ]

        # Normalise content
        full_text = "".join(text_chunks)
        content = [{"type": "text", "text": full_text}] if full_text else []

        return {"content": content, "tool_uses": tool_uses}
