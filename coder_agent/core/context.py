"""Context window management: MessageHistory + rule-based compression.

Merges history_util.py and compression.py from the original codebase.
"""

from dataclasses import dataclass
from typing import Any

from coder_agent.config import cfg


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

_FILE_CONTENT_THRESHOLD = 30
_TERMINAL_THRESHOLD = 40
_SHORT_THRESHOLD = 10
_FILE_HEAD_LINES = 10
_FILE_TAIL_LINES = 5
_TERMINAL_TAIL_LINES = 30


@dataclass
class CompressionResult:
    content: str
    was_compressed: bool
    original_chars: int
    compressed_chars: int

    @property
    def ratio(self) -> float:
        if self.original_chars == 0:
            return 1.0
        return self.compressed_chars / self.original_chars


def _is_terminal_output(content: str) -> bool:
    if content.startswith("Exit code:"):
        return True
    return any(kw in content for kw in ("PASSED", "FAILED", "ERROR", "Traceback"))


def _compress_file_content(lines: list[str]) -> str:
    if len(lines) <= _FILE_CONTENT_THRESHOLD:
        return "\n".join(lines)
    head = lines[:_FILE_HEAD_LINES]
    tail = lines[-_FILE_TAIL_LINES:]
    omitted = len(lines) - _FILE_HEAD_LINES - _FILE_TAIL_LINES
    return "\n".join(head + [f"... ({omitted} lines omitted) ..."] + tail)


def _compress_terminal(lines: list[str]) -> str:
    if len(lines) <= _TERMINAL_THRESHOLD:
        return "\n".join(lines)
    tail = lines[-_TERMINAL_TAIL_LINES:]
    omitted = len(lines) - _TERMINAL_TAIL_LINES
    return "\n".join([f"... ({omitted} earlier lines omitted) ..."] + tail)


def compress_observation(content: str) -> CompressionResult:
    original_chars = len(content)
    lines = content.splitlines()

    if len(lines) <= _SHORT_THRESHOLD:
        return CompressionResult(content, False, original_chars, original_chars)

    compressed = _compress_terminal(lines) if _is_terminal_output(content) else _compress_file_content(lines)
    return CompressionResult(
        content=compressed,
        was_compressed=compressed != content,
        original_chars=original_chars,
        compressed_chars=len(compressed),
    )


# ---------------------------------------------------------------------------
# Message History
# ---------------------------------------------------------------------------

class MessageHistory:
    """Conversation history with token-aware truncation."""

    def __init__(self, model: str, system: str, context_window_tokens: int, client: Any):
        self.model = model
        self.system = system
        self.context_window_tokens = context_window_tokens
        self.client = client
        self.messages: list[dict] = []
        self.total_tokens = len(system) // 4
        self.message_tokens: list[tuple[int, int]] = []

    async def add_message(
        self,
        role: str,
        content: str | list[dict[str, Any]],
        usage: Any | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        if role == "tool":
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
        compression_strategy = cfg.context.compression_strategy
        while self.total_tokens > self.context_window_tokens and self.messages:
            compressed = False
            if compression_strategy != "disabled":
                for i, msg in enumerate(self.messages):
                    if msg.get("role") == "tool" and not str(msg.get("content", "")).startswith("[COMPRESSED]"):
                        result = compress_observation(str(msg["content"]))
                        if result.was_compressed:
                            self.messages[i] = {**msg, "content": f"[COMPRESSED] {result.content}"}
                            saved = (result.original_chars - result.compressed_chars) // 4
                            self.total_tokens = max(0, self.total_tokens - saved)
                            compressed = True
                            break

            if not compressed:
                self.messages.pop(0)
                if self.message_tokens:
                    input_tokens, output_tokens = self.message_tokens.pop(0)
                    self.total_tokens -= input_tokens + output_tokens

        if self.messages:
            notice = {"role": "user", "content": "[Earlier history has been truncated.]"}
            if self.messages[0] != notice:
                self.messages.insert(0, notice)

    def format_for_api(self) -> list[dict[str, Any]]:
        return self.messages.copy()
