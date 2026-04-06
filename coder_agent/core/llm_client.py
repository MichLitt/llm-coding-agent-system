"""LLM backend client — supports OpenAI-compatible and Anthropic-compatible APIs.

Backend selection is driven by ``cfg.model.api_format``:

  "openai"     → _OpenAIBackend  (MiniMax M2.5 and other OpenAI-compat endpoints)
  "anthropic"  → _AnthropicBackend (MiniMax M2.7 via Token Plan)

Both backends normalise responses to the same dict:
    {
        "content":      [{"type": "text", "text": "..."}],
        "tool_uses":    [{"id": "...", "name": "...", "input": {...}}],
        "parse_errors": ["..."],
    }

Switching models:
    # .env for M2.7 (default)
    ANTHROPIC_API_KEY=<token_plan_key>
    ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic   # default, can omit

    # .env for M2.5 fallback
    LLM_API_KEY=<key>
    LLM_BASE_URL=<base_url>
    CODER_MODEL=MiniMax-M2.5
    CODER_API_FORMAT=openai
"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
from collections import defaultdict
from json import JSONDecodeError
from typing import Any

import anthropic
import openai
from openai import AsyncOpenAI

from coder_agent.config import LLMProfile, resolve_llm_profile


# ---------------------------------------------------------------------------
# Shared JSON helpers (used by _OpenAIBackend)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _OpenAIBackend
# ---------------------------------------------------------------------------

class _OpenAIBackend:
    """Async OpenAI-compatible backend (MiniMax M2.5 and other OpenAI endpoints)."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client: AsyncOpenAI | None = None
        self._client_loop_id: int | None = None

    def _build_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

    def _client_for_current_loop(self) -> AsyncOpenAI:
        loop_id = id(asyncio.get_running_loop())
        if self._client is None or self._client_loop_id != loop_id:
            self._client = self._build_client()
            self._client_loop_id = loop_id
        return self._client

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

        client = self._client_for_current_loop()

        _MAX_RETRIES = 3
        _RETRY_DELAYS = [1.0, 2.0, 4.0]
        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                delay = _RETRY_DELAYS[attempt - 1] + random.uniform(0.0, 0.5)
                await asyncio.sleep(delay)
            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=full_messages,
                    tools=openai_tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                )
                break
            except (openai.APIConnectionError, openai.APITimeoutError):
                if attempt == _MAX_RETRIES:
                    raise

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
                tool_call["arguments"], tool_call["name"]
            )
            if parse_error is not None:
                parse_errors.append(parse_error)
                continue
            tool_uses.append({"id": tool_call["id"], "name": tool_call["name"], "input": parsed_input or {}})

        full_text = "".join(text_chunks)
        content = [{"type": "text", "text": full_text}] if full_text else []
        return {"content": content, "tool_uses": tool_uses, "parse_errors": parse_errors}

    async def aclose(self) -> None:
        if self._client is None:
            return
        client = self._client
        self._client = None
        self._client_loop_id = None
        close = getattr(client, "close", None)
        try:
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
            else:
                inner_client = getattr(client, "_client", None)
                if inner_client is not None:
                    inner_close = getattr(inner_client, "aclose", None)
                    if inner_close is not None:
                        result = inner_close()
                        if inspect.isawaitable(result):
                            await result
        except RuntimeError as exc:
            if "Event loop is closed" not in str(exc):
                raise


# ---------------------------------------------------------------------------
# _AnthropicBackend
# ---------------------------------------------------------------------------

def _normalize_messages_for_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-format message history to Anthropic message format.

    The agent stores messages in OpenAI format (tool_calls on assistant messages,
    role="tool" for results). The Anthropic API requires a different structure:
    - assistant messages with tool calls → content list with tool_use blocks
    - consecutive role="tool" messages → single user message with tool_result blocks
    - error_kind is stripped (internal field not recognized by Anthropic)

    Also handles already-Anthropic-format messages (content as a list with
    tool_use/tool_result blocks) by stripping internal fields like error_kind.
    """
    result: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if not pending_tool_results:
            return
        result.append({"role": "user", "content": pending_tool_results[:]})
        pending_tool_results.clear()

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")  # OpenAI-format tool calls on assistant msgs

        # --- OpenAI-format tool result: role="tool" ---
        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            tool_result_block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": str(content),
            }
            if msg.get("is_error"):
                tool_result_block["is_error"] = True
            pending_tool_results.append(tool_result_block)
            continue

        # Any non-tool message flushes pending tool results first
        flush_tool_results()

        # --- OpenAI-format assistant message with tool_calls ---
        if role == "assistant" and tool_calls:
            content_blocks: list[dict[str, Any]] = []
            if content:
                content_blocks.append({"type": "text", "text": str(content)})
            for tc in tool_calls:
                fn = tc.get("function", {})
                raw_args = fn.get("arguments", "{}")
                try:
                    parsed_input = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (json.JSONDecodeError, TypeError):
                    parsed_input = {}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": parsed_input,
                })
            result.append({"role": "assistant", "content": content_blocks})
            continue

        # --- Already-Anthropic content list (strip internal fields) ---
        if isinstance(content, list):
            new_content: list[dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    clean: dict[str, Any] = {
                        "type": "tool_result",
                        "tool_use_id": block.get("tool_use_id", ""),
                        "content": block.get("content", ""),
                    }
                    if block.get("is_error"):
                        clean["is_error"] = True
                    new_content.append(clean)
                else:
                    new_content.append(block)
            result.append({**msg, "content": new_content})
            continue

        # --- Plain text user/assistant message ---
        result.append(msg)

    flush_tool_results()
    return result


class _AnthropicBackend:
    """Async Anthropic-compatible backend (MiniMax M2.7 via Token Plan).

    Uses the ``anthropic`` SDK with a custom ``base_url`` pointing at
    ``https://api.minimax.io/anthropic``.

    Tool inputs are returned as pre-parsed dicts by the Anthropic API, so
    no JSON parsing is needed on the client side.
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_loop_id: int | None = None

    def _build_client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    def _client_for_current_loop(self) -> anthropic.AsyncAnthropic:
        loop_id = id(asyncio.get_running_loop())
        if self._client is None or self._client_loop_id != loop_id:
            self._client = self._build_client()
            self._client_loop_id = loop_id
        return self._client

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
        normalized = _normalize_messages_for_anthropic(messages)
        # Tools are already in Anthropic format (name/description/input_schema)
        anthropic_tools = tools if tools else anthropic.NOT_GIVEN

        client = self._client_for_current_loop()

        _MAX_RETRIES = 3
        _RETRY_DELAYS = [1.0, 2.0, 4.0]
        final_message: anthropic.types.Message | None = None

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                delay = _RETRY_DELAYS[attempt - 1] + random.uniform(0.0, 0.5)
                await asyncio.sleep(delay)
            try:
                async with client.messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=normalized,
                    tools=anthropic_tools,
                ) as stream:
                    if on_token:
                        async for text in stream.text_stream:
                            await on_token(text)
                    else:
                        # drain the stream so the connection completes
                        async for _ in stream.text_stream:
                            pass
                    final_message = await stream.get_final_message()
                break
            except (anthropic.APIConnectionError, anthropic.APITimeoutError):
                if attempt == _MAX_RETRIES:
                    raise

        if final_message is None:
            return {"content": [], "tool_uses": [], "parse_errors": ["No response received"]}

        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []

        for block in final_message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,  # already a parsed dict
                })

        full_text = "".join(text_parts)
        content = [{"type": "text", "text": full_text}] if full_text else []
        return {"content": content, "tool_uses": tool_uses, "parse_errors": []}

    async def aclose(self) -> None:
        if self._client is None:
            return
        client = self._client
        self._client = None
        self._client_loop_id = None
        try:
            await client.close()
        except RuntimeError as exc:
            if "Event loop is closed" not in str(exc):
                raise


# ---------------------------------------------------------------------------
# LLMClient — public facade
# ---------------------------------------------------------------------------

class LLMClient:
    """Public facade: selects the correct backend based on the resolved LLMProfile.

    Pass an explicit ``LLMProfile`` to bind this client to a specific provider.
    If omitted, ``resolve_llm_profile()`` is called to derive the profile from
    config.yaml and environment variables.

    The chat() interface is identical regardless of backend, so callers
    (Agent, AgentSession) are unaffected by the provider switch.
    """

    def __init__(self, profile: LLMProfile | None = None) -> None:
        self.profile: LLMProfile = profile if profile is not None else resolve_llm_profile()
        if self.profile.transport == "anthropic":
            self._backend: _OpenAIBackend | _AnthropicBackend = _AnthropicBackend(
                api_key=self.profile.api_key,
                base_url=self.profile.base_url or "https://api.minimax.io/anthropic",
            )
        else:
            self._backend = _OpenAIBackend(
                api_key=self.profile.api_key,
                base_url=self.profile.base_url or "",
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
        return await self._backend.chat(
            messages=messages,
            system=system,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            on_token=on_token,
        )

    async def aclose(self) -> None:
        await self._backend.aclose()

    def close(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(self.aclose())
            except RuntimeError as exc:
                if "Event loop is closed" not in str(exc):
                    raise
            return
        loop.create_task(self.aclose())
