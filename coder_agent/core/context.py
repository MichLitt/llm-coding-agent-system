"""Context window management: MessageHistory + rule-based compression.

Merges history_util.py and compression.py from the original codebase.
"""

import ast
import json
import re
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
_TEST_SUMMARY_RE = re.compile(r"\b\d+\s+passed\b.*\bin\b", re.IGNORECASE)
_PASSED_COUNT_RE = re.compile(r"\b(\d+)\s+passed\b", re.IGNORECASE)
_FAILURE_MARKER_RE = re.compile(r"^\s*(FAILED|ERROR)\b")
_PYTHON_SIGNATURE_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+")


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


def _context_setting(
    key: str,
    default: Any,
    experiment_config: dict[str, Any] | None = None,
) -> Any:
    if experiment_config and key in experiment_config:
        return experiment_config[key]
    return getattr(cfg.context, key, default)


def _is_tool_call_assistant(message: dict[str, Any]) -> bool:
    return message.get("role") == "assistant" and bool(message.get("tool_calls"))


def _is_tool_result(message: dict[str, Any]) -> bool:
    return message.get("role") == "tool"


class HistoryProtocolError(ValueError):
    """The history would be rejected by a strict OpenAI-compatible API."""


def validate_tool_call_sequence(messages: list[dict[str, Any]]) -> None:
    """Every assistant tool call must be answered by a tool result right after it.

    GLM accepts a message between the calls and their results; DeepSeek rejects
    the request with HTTP 400. Checking before each request turns such a bug
    into a clear error instead of a provider failure.
    """
    index = 0
    while index < len(messages):
        message = messages[index]
        if _is_tool_result(message):
            raise HistoryProtocolError(
                f"message {index}: tool result {message.get('tool_call_id')!r} "
                "does not follow the assistant message that called it"
            )
        index += 1
        if _is_tool_call_assistant(message):
            expected = [str(call.get("id")) for call in message["tool_calls"]]
            answered = []
            while index < len(messages) and _is_tool_result(messages[index]):
                answered.append(str(messages[index].get("tool_call_id")))
                index += 1
            if sorted(answered) != sorted(expected):
                raise HistoryProtocolError(
                    f"message {index}: tool calls {expected} are followed by results {answered}"
                )


def estimate_tokens(text: str) -> int:
    # About three characters per token for code and logs; deliberately high, so
    # truncation starts before a provider's context limit rather than after it.
    return len(text) // 3


def cut_observation(content: str, max_chars: int) -> str:
    """Keep the head and tail of an output longer than ``max_chars``."""
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    half = max_chars // 2
    omitted = len(content) - 2 * half
    return (
        f"{content[:half]}\n[... {omitted} characters cut: the output was too long. "
        "Narrow it with grep, sed -n, head or a read_file line range. ...]\n"
        f"{content[-half:]}"
    )


def _message_tokens(message: dict[str, Any]) -> int:
    tokens = estimate_tokens(str(message.get("content", "")))
    if message.get("tool_calls"):
        tokens += estimate_tokens(json.dumps(message["tool_calls"]))
    return tokens


def _find_safe_compaction_split(messages: list[dict[str, Any]], raw_split: int) -> int | None:
    split_index = max(0, min(raw_split, len(messages)))
    while split_index > 0:
        previous = messages[split_index - 1]
        current = messages[split_index] if split_index < len(messages) else None
        if _is_tool_call_assistant(previous):
            split_index -= 1
            continue
        if current is not None and _is_tool_result(current):
            split_index -= 1
            continue
        return split_index
    return None


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


def _looks_like_python_source(lines: list[str]) -> bool:
    return any(_PYTHON_SIGNATURE_RE.match(line) for line in lines)


def _compress_terminal_smart(lines: list[str]) -> str:
    failure_indices = [i for i, line in enumerate(lines) if _FAILURE_MARKER_RE.match(line)]
    if not failure_indices:
        return _compress_terminal(lines)

    ranges: list[tuple[int, int]] = []
    for index in failure_indices:
        start = max(0, index - 10)
        end = min(len(lines), index + 11)
        if ranges and start <= ranges[-1][1]:
            prev_start, prev_end = ranges[-1]
            ranges[-1] = (prev_start, max(prev_end, end))
        else:
            ranges.append((start, end))
        if len(ranges) >= 3:
            break

    summary_line = next((line for line in reversed(lines) if _TEST_SUMMARY_RE.search(line)), None)
    output: list[str] = []
    if summary_line is not None:
        match = _PASSED_COUNT_RE.search(summary_line)
        if match and int(match.group(1)) > 0:
            output.append(f"[{match.group(1)} passing tests omitted]")

    for start, end in ranges:
        block = lines[start:end]
        while block and not block[0].strip():
            block = block[1:]
        while block and not block[-1].strip():
            block = block[:-1]
        if not block:
            continue
        if output:
            output.append("")
        output.extend(block)

    if summary_line is not None and (not output or output[-1] != summary_line):
        if output:
            output.append("")
        output.append(summary_line)

    return "\n".join(output).strip() or _compress_terminal(lines)


def _compress_file_smart(lines: list[str]) -> str:
    original = "\n".join(lines)
    try:
        tree = ast.parse(original)
    except SyntaxError:
        return _compress_file_content(lines)

    import_block: list[str] = []
    import_start: int | None = None
    import_end = 0
    for node in tree.body:
        is_module_docstring = (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if import_start is None and is_module_docstring:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_start = node.lineno if import_start is None else import_start
            import_end = node.end_lineno or node.lineno
            continue
        break
    if import_start is not None:
        import_block = lines[import_start - 1:import_end]

    entries: list[str] = []

    def visit(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                signature_line = lines[node.lineno - 1]
                indent = len(signature_line) - len(signature_line.lstrip(" "))
                entries.append(signature_line)

                has_docstring = (
                    bool(node.body)
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                )
                if has_docstring:
                    entries.append(lines[node.body[0].lineno - 1])

                total_body_lines = max(0, (node.end_lineno or node.lineno) - node.lineno)
                omitted_body_lines = max(0, total_body_lines - (1 if has_docstring else 0))
                if omitted_body_lines > 0:
                    entries.append(f"{' ' * (indent + 4)}[body: {omitted_body_lines} lines]")

                visit(node.body)

    visit(tree.body)

    if not entries:
        return original

    compressed_lines: list[str] = []
    if import_block:
        compressed_lines.extend(import_block)
    if import_block and entries:
        compressed_lines.append("")
    compressed_lines.extend(entries)

    compressed = "\n".join(compressed_lines).rstrip()
    if len(compressed) >= len(original):
        return original
    return compressed


def compress_observation(
    content: str,
    experiment_config: dict[str, Any] | None = None,
) -> CompressionResult:
    original_chars = len(content)
    lines = content.splitlines()

    if len(lines) <= _SHORT_THRESHOLD:
        return CompressionResult(content, False, original_chars, original_chars)

    mode = _context_setting("observation_compression_mode", "off", experiment_config)
    if mode == "off":
        return CompressionResult(content, False, original_chars, original_chars)
    if mode == "smart":
        if _is_terminal_output(content):
            compressed = _compress_terminal_smart(lines)
        elif _looks_like_python_source(lines):
            compressed = _compress_file_smart(lines)
        else:
            compressed = _compress_file_content(lines)
    else:
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
    """Conversation history with token-aware truncation.

    ``message_tokens`` holds each message's estimated size as (tokens, 0), and
    ``total_tokens`` the estimated size of the whole request: the system prompt
    plus every message. Provider usage is not used: a call's input tokens
    already include all earlier messages, so summing them overcounts.
    """

    def __init__(
        self,
        model: str,
        system: str,
        context_window_tokens: int,
        client: Any,
        experiment_config: dict[str, Any] | None = None,
    ):
        self.model = model
        self.system = system
        self.context_window_tokens = context_window_tokens
        self.client = client
        self.experiment_config = dict(experiment_config or {})
        self.messages: list[dict] = []
        self.total_tokens = estimate_tokens(system)
        self.message_tokens: list[tuple[int, int]] = []

    async def add_message(
        self,
        role: str,
        content: str | list[dict[str, Any]],
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        if role == "tool":
            tool_call_id = tool_calls[0]["id"] if tool_calls else ""
            compressed = compress_observation(str(content), self.experiment_config)
            max_chars = int(_context_setting("max_observation_chars", 0, self.experiment_config) or 0)
            msg = {"role": "tool", "tool_call_id": tool_call_id, "content": cut_observation(compressed.content, max_chars)}
        else:
            msg = {"role": role, "content": content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
        self.insert_message(len(self.messages), msg)

    def insert_message(self, index: int, message: dict[str, Any]) -> None:
        tokens = _message_tokens(message)
        self.messages.insert(index, message)
        self.message_tokens.insert(index, (tokens, 0))
        self.total_tokens += tokens

    def recount_tokens(self) -> None:
        """Recompute the estimates after messages were edited in place."""
        self.message_tokens = [(_message_tokens(message), 0) for message in self.messages]
        self.total_tokens = estimate_tokens(self.system) + sum(tokens for tokens, _ in self.message_tokens)

    def _preamble_length(self) -> int:
        # The task: the user messages before the first assistant turn.
        for index, message in enumerate(self.messages):
            if message.get("role") != "user":
                return index
        return len(self.messages)

    def _compress_one_observation(self) -> bool:
        for i, msg in enumerate(self.messages):
            if msg.get("role") == "tool" and not str(msg.get("content", "")).startswith("[COMPRESSED]"):
                result = compress_observation(str(msg["content"]), self.experiment_config)
                if result.was_compressed:
                    self.messages[i] = {**msg, "content": f"[COMPRESSED] {result.content}"}
                    tokens = _message_tokens(self.messages[i])
                    self.total_tokens -= self.message_tokens[i][0] - tokens
                    self.message_tokens[i] = (tokens, 0)
                    return True
        return False

    def truncate(self) -> None:
        """Drop the oldest turns until the history fits the context window.

        A turn is an assistant message with everything up to the next one: its
        tool results and any feedback. Dropping whole turns keeps the history a
        valid request. The task before the first turn and the latest turn are kept.
        """
        compression_strategy = _context_setting("compression_strategy", "rule_based", self.experiment_config)
        notice = {"role": "user", "content": "[Earlier history has been truncated.]"}
        did_truncate = False
        while self.total_tokens > self.context_window_tokens:
            if compression_strategy != "disabled" and self._compress_one_observation():
                continue
            start = self._preamble_length()
            end = start + 1
            while end < len(self.messages) and self.messages[end].get("role") != "assistant":
                end += 1
            if end >= len(self.messages):
                break
            for tokens, _ in self.message_tokens[start:end]:
                self.total_tokens -= tokens
            del self.messages[start:end]
            del self.message_tokens[start:end]
            did_truncate = True

        if did_truncate and notice not in self.messages:
            self.insert_message(self._preamble_length(), notice)

    async def compact(self, client: Any, params: dict, keep_recent: int = 6) -> None:
        if "model" not in params:
            raise ValueError("params must include 'model'")
        if len(self.messages) <= keep_recent:
            return

        raw_split = len(self.messages) if keep_recent <= 0 else len(self.messages) - keep_recent
        split_index = _find_safe_compaction_split(self.messages, raw_split)
        if split_index is None or split_index <= 1:
            return

        to_compress = self.messages[:split_index]
        to_keep = self.messages[split_index:]
        tokens_to_compress = self.message_tokens[:split_index]
        tokens_to_keep = self.message_tokens[split_index:]

        summary_response = await client.chat(
            messages=to_compress,
            system=(
                "Summarize the agent's work so far as a structured JSON object with keys: "
                "task_goal, completed_steps (list), files_modified (list), current_state, "
                "failed_approaches (list), open_issues (list). "
                "Be concise. Each list item <= 1 sentence."
            ),
            tools=[],
            **{k: v for k, v in params.items() if k in ("model", "max_tokens", "temperature", "seed")},
        )
        if hasattr(summary_response, "content") and summary_response.content:
            block = summary_response.content[0]
            summary_text = block.text if hasattr(block, "text") else str(block)
        else:
            summary_text = str(summary_response)

        summary_msg = {
            "role": "user",
            "content": f"[Context compacted — {len(to_compress)} messages summarized]\n{summary_text}",
        }
        compressed_tokens = sum(tokens for tokens, _ in tokens_to_compress)
        summary_est_tokens = _message_tokens(summary_msg)

        self.messages = [summary_msg] + list(to_keep)
        self.message_tokens = [(summary_est_tokens, 0)] + list(tokens_to_keep)
        self.total_tokens = max(0, (self.total_tokens - compressed_tokens) + summary_est_tokens)

    def format_for_api(self) -> list[dict[str, Any]]:
        return self.messages.copy()
