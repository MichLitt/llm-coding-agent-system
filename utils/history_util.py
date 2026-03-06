"""Message history management with token tracking and context-window truncation.

MessageHistory keeps track of the full conversation (user / assistant /
tool_result turns) and knows how many tokens are currently in use.
When the running total exceeds context_window_tokens, it drops the oldest
user+assistant turn pair and inserts a truncation notice in its place.

Responsibilities
----------------
- add_message(role, content, usage)  — append a message; update token count
- truncate()                          — shed oldest pairs until under the limit
- format_for_api()                    — produce the messages list for the LLM backend
"""

from typing import Any

import config
from utils.compression import compress_observation


class MessageHistory:
    """Conversation history with token-aware truncation.

    Attributes
    ----------
    model : str
        Model name passed to the LLM backend.
    system : str
        System prompt (its token count is subtracted from the budget).
    context_window_tokens : int
        Hard cap; truncate() fires when total_tokens exceeds this.
    client : Any
        LLM backend client; used for token counting if the backend supports it.
    messages : list[dict]
        The raw conversation turns in Claude API format.
    total_tokens : int
        Running estimate of tokens consumed by the current history.
    message_tokens : list[tuple[int, int]]
        Per-turn (input_tokens, output_tokens) pairs for precise eviction.
    """

    def __init__(
        self,
        model: str,
        system: str,
        context_window_tokens: int,
        client: Any,
    ):
        self.model = model
        self.system = system
        self.context_window_tokens = context_window_tokens
        self.client = client
        self.messages = []
        self.total_tokens = len(system) // 4  # system prompt token estimate
        self.message_tokens = []

    async def add_message(
        self,
        role: str,
        content: str | list[dict[str, Any]],
        usage: Any | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """Append a message and update the running token count.

        For assistant turns, usage (from the API response) is used to update
        message_tokens and total_tokens precisely. Pass tool_calls to record
        tool invocations in OpenAI format (required for tool_result correlation).
        """
        if role == "tool":
            # OpenAI tool result format: one message per tool call
            tool_call_id = tool_calls[0]["id"] if tool_calls else ""
            msg = {"role": "tool", "tool_call_id": tool_call_id, "content": str(content)}
        else:
            msg = {"role": role, "content": content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
        self.messages.append(msg)
        if role == "assistant" and usage is not None:
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            self.message_tokens.append((input_tokens, output_tokens))
            self.total_tokens += input_tokens + output_tokens
        
    def truncate(self) -> None:
        """Reduce history to fit within the token budget.

        Strategy (in order of preference):
        1. Compress the earliest uncompressed tool message using compression.py rules.
        2. If no compressible message found, drop the oldest user+assistant+tool group.

        Inserts a truncation notice at the start so the model knows context was dropped.
        """
        while self.total_tokens > self.context_window_tokens and self.messages:
            # Step 1: find the earliest tool message not yet compressed
            compressed = False
            if config.COMPRESSION_STRATEGY != "disabled":
                for i, msg in enumerate(self.messages):
                    if msg.get("role") == "tool" and not str(msg.get("content", "")).startswith("[COMPRESSED]"):
                        result = compress_observation(str(msg["content"]))
                        if result.was_compressed:
                            self.messages[i] = {**msg, "content": f"[COMPRESSED] {result.content}"}
                            # Rough token delta (4 chars ≈ 1 token)
                            saved = (result.original_chars - result.compressed_chars) // 4
                            self.total_tokens = max(0, self.total_tokens - saved)
                            compressed = True
                            break

            if not compressed:
                # Step 2: drop oldest messages until we remove a full group
                self.messages.pop(0)
                if self.message_tokens:
                    input_tokens, output_tokens = self.message_tokens.pop(0)
                    self.total_tokens -= input_tokens + output_tokens

        if self.messages:
            notice = {"role": "user", "content": "[Earlier history has been truncated.]"}
            if self.messages[0] != notice:
                self.messages.insert(0, notice)

    def format_for_api(self) -> list[dict[str, Any]]:
        """Return messages in the format expected by the LLM backend."""
        return self.messages.copy()
