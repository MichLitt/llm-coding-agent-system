"""LLM backend client using OpenAI-compatible API."""

import json
from collections import defaultdict
from json import JSONDecodeError
from typing import Any

from openai import AsyncOpenAI

from coder_agent.config import cfg


def _extract_balanced_json_object(raw: str) -> str | None:
    """Return the first balanced JSON object embedded in *raw*."""
    for start, char in enumerate(raw):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(raw)):
            current = raw[end]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    return raw[start : end + 1]
    return None


def _parse_tool_arguments(arguments: str, tool_name: str) -> tuple[dict[str, Any] | None, str | None]:
    """Best-effort parse of streamed tool arguments."""
    raw = (arguments or "").strip()
    candidates: list[str] = []
    if raw:
        candidates.append(raw)
        extracted = _extract_balanced_json_object(raw)
        if extracted and extracted not in candidates:
            candidates.append(extracted)
    else:
        candidates.append("{}")

    last_error = "tool arguments were empty"
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except JSONDecodeError as exc:
            last_error = f"{exc.msg} at char {exc.pos}"
            continue
        if isinstance(parsed, dict):
            return parsed, None
        last_error = f"expected JSON object, got {type(parsed).__name__}"

    snippet = raw[:200] if raw else "<empty>"
    return None, f"{tool_name or 'unknown_tool'}: malformed tool arguments ({last_error}). Raw: {snippet}"


class LLMClient:
    """Async OpenAI-compatible client.

    Normalises responses to:
        {
            "content": [{"type": "text", "text": "..."}],
            "tool_uses": [{"id": "...", "name": "...", "input": {...}}],
            "parse_errors": ["..."],
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

        tool_uses: list[dict[str, Any]] = []
        parse_errors: list[str] = []
        for idx in sorted(tc_accum):
            tool_call = tc_accum[idx]
            parsed_input, parse_error = _parse_tool_arguments(
                tool_call["arguments"],
                tool_call["name"],
            )
            if parse_error is not None:
                parse_errors.append(parse_error)
                continue
            tool_uses.append(
                {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": parsed_input or {},
                }
            )

        full_text = "".join(text_chunks)
        content = [{"type": "text", "text": full_text}] if full_text else []

        return {"content": content, "tool_uses": tool_uses, "parse_errors": parse_errors}
