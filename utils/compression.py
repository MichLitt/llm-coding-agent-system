"""Rule-based compression for tool result messages.

Reduces token usage by compressing long tool outputs before they are stored
in MessageHistory. No LLM calls — all heuristic rules.

Design
------
Three message categories and their compression strategies:

  file_content   : keep first 10 lines + last 5 lines, drop middle
  terminal_output: keep last 30 lines (errors are at the bottom)
  short_message  : return as-is (already small enough)

The caller (MessageHistory.truncate) decides WHEN to compress; this module
decides HOW to compress a single message.

Usage
-----
    from utils.compression import compress_observation, CompressionResult

    result = compress_observation(content)
    if result.was_compressed:
        store(result.content)
"""

from dataclasses import dataclass


# Thresholds (lines)
_FILE_CONTENT_THRESHOLD = 30   # compress if more lines than this
_TERMINAL_THRESHOLD = 40       # compress if more lines than this
_SHORT_THRESHOLD = 10          # never compress below this many lines

_FILE_HEAD_LINES = 10
_FILE_TAIL_LINES = 5
_TERMINAL_TAIL_LINES = 30


@dataclass
class CompressionResult:
    """Outcome of a single compress_observation() call."""
    content: str          # compressed (or original) text
    was_compressed: bool  # True if content was actually shortened
    original_chars: int   # character count before compression
    compressed_chars: int # character count after compression

    @property
    def ratio(self) -> float:
        """Compression ratio: compressed / original (lower = more compressed)."""
        if self.original_chars == 0:
            return 1.0
        return self.compressed_chars / self.original_chars


def compress_observation(content: str) -> CompressionResult:
    """Compress a tool result string using heuristic rules.

    Automatically detects the content category and applies the appropriate
    strategy. Returns a CompressionResult with the (possibly shortened) text.

    Parameters
    ----------
    content : str
        Raw tool result content (stdout, file content, success message, etc.)

    Returns
    -------
    CompressionResult
    """
    original_chars = len(content)
    lines = content.splitlines()

    if len(lines) <= _SHORT_THRESHOLD:
        # Already short — nothing to compress
        return CompressionResult(
            content=content,
            was_compressed=False,
            original_chars=original_chars,
            compressed_chars=original_chars,
        )

    if _is_terminal_output(content, lines):
        compressed = _compress_terminal(lines)
    else:
        # Treat as file content by default
        compressed = _compress_file_content(lines)

    return CompressionResult(
        content=compressed,
        was_compressed=compressed != content,
        original_chars=original_chars,
        compressed_chars=len(compressed),
    )


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

def _is_terminal_output(content: str, lines: list[str]) -> bool:
    """Heuristic: does this look like shell/terminal output?

    Terminal output typically contains:
    - "Exit code:" prefix (our run_command format)
    - "STDOUT:" / "STDERR:" headers
    - Lines starting with common shell prefixes
    """
    # Our run_command tool always starts with "Exit code:"
    if content.startswith("Exit code:"):
        return True
    # Pytest / compiler output patterns
    if any(kw in content for kw in ("PASSED", "FAILED", "ERROR", "Traceback")):
        return True
    return False


# ---------------------------------------------------------------------------
# Compression strategies
# ---------------------------------------------------------------------------

def _compress_file_content(lines: list[str]) -> str:
    """Keep first HEAD lines + last TAIL lines, replace middle with a notice.

    Example (HEAD=10, TAIL=5, total=100 lines):
        lines 1-10
        ... (85 lines omitted) ...
        lines 96-100
    """
    if len(lines) <= _FILE_CONTENT_THRESHOLD:
        return "\n".join(lines)

    head = lines[:_FILE_HEAD_LINES]
    tail = lines[-_FILE_TAIL_LINES:]
    omitted = len(lines) - _FILE_HEAD_LINES - _FILE_TAIL_LINES
    notice = f"... ({omitted} lines omitted) ..."
    return "\n".join(head + [notice] + tail)


def _compress_terminal(lines: list[str]) -> str:
    """Keep only the last TAIL lines of terminal output.

    Errors and test failures appear at the end, so the tail is most useful.
    Prepend a notice so the model knows context was dropped.
    """
    if len(lines) <= _TERMINAL_THRESHOLD:
        return "\n".join(lines)

    tail = lines[-_TERMINAL_TAIL_LINES:]
    omitted = len(lines) - _TERMINAL_TAIL_LINES
    notice = f"... ({omitted} earlier lines omitted) ..."
    return "\n".join([notice] + tail)
