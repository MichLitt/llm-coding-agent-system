"""Context window management: MessageHistory + rule-based compression.

Merges history_util.py and compression.py from the original codebase.
"""

import ast
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

    mode = _context_setting("observation_compression_mode", "rule_based", experiment_config)
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
    """Conversation history with token-aware truncation."""

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
            compressed = compress_observation(str(content), self.experiment_config)
            msg = {"role": "tool", "tool_call_id": tool_call_id, "content": compressed.content}
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
        else:
            self.message_tokens.append((0, 0))

    def truncate(self) -> None:
        compression_strategy = _context_setting("compression_strategy", "rule_based", self.experiment_config)
        did_truncate = False
        while self.total_tokens > self.context_window_tokens and self.messages:
            compressed = False
            if compression_strategy != "disabled":
                for i, msg in enumerate(self.messages):
                    if msg.get("role") == "tool" and not str(msg.get("content", "")).startswith("[COMPRESSED]"):
                        result = compress_observation(str(msg["content"]), self.experiment_config)
                        if result.was_compressed:
                            self.messages[i] = {**msg, "content": f"[COMPRESSED] {result.content}"}
                            saved = (result.original_chars - result.compressed_chars) // 4
                            self.total_tokens = max(0, self.total_tokens - saved)
                            compressed = True
                            break

            if not compressed:
                self.messages.pop(0)
                input_tokens, output_tokens = self.message_tokens.pop(0)
                self.total_tokens -= input_tokens + output_tokens
                did_truncate = True

        if did_truncate and self.messages:
            notice = {"role": "user", "content": "[Earlier history has been truncated.]"}
            if self.messages[0] != notice:
                self.messages.insert(0, notice)
                self.message_tokens.insert(0, (0, 0))

    async def compact(self, client: Any, params: dict, keep_recent: int = 6) -> None:
        if "model" not in params:
            raise ValueError("params must include 'model'")
        if len(self.messages) <= keep_recent:
            return

        if keep_recent <= 0:
            to_compress = self.messages
            to_keep: list[dict[str, Any]] = []
            tokens_to_compress = self.message_tokens
            tokens_to_keep: list[tuple[int, int]] = []
        else:
            to_compress = self.messages[:-keep_recent]
            to_keep = self.messages[-keep_recent:]
            tokens_to_compress = self.message_tokens[:-keep_recent]
            tokens_to_keep = self.message_tokens[-keep_recent:]

        summary_response = await client.chat(
            messages=to_compress,
            system=(
                "Summarize the agent's work so far as a structured JSON object with keys: "
                "task_goal, completed_steps (list), files_modified (list), current_state, "
                "failed_approaches (list), open_issues (list). "
                "Be concise. Each list item <= 1 sentence."
            ),
            tools=[],
            **{k: v for k, v in params.items() if k in ("model", "max_tokens", "temperature")},
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
        compressed_tokens = sum(input_tokens + output_tokens for input_tokens, output_tokens in tokens_to_compress)
        summary_est_tokens = len(summary_text) // 4

        self.messages = [summary_msg] + list(to_keep)
        self.message_tokens = [(0, summary_est_tokens)] + list(tokens_to_keep)
        self.total_tokens = max(0, (self.total_tokens - compressed_tokens) + summary_est_tokens)

    def format_for_api(self) -> list[dict[str, Any]]:
        return self.messages.copy()
