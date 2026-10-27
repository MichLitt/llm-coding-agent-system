"""Exact-resume checkpoint payloads: message-history snapshots and workspace fingerprints.

A step checkpoint stores the full ``MessageHistory`` state, not an append-only
message log. History is mutated in place by truncation, compaction and
approach-memory injection, so replaying an append log would also need those
mutation events. A snapshot taken at a step boundary restores in O(1).

Snapshots are zlib-compressed canonical JSON with a schema version. They are
only taken at step boundaries, where every assistant tool call already has its
tool result, so a restored history is always valid to send back to the API.
"""

from __future__ import annotations

import hashlib
import json
import os
import zlib
from pathlib import Path
from typing import Any

HISTORY_SNAPSHOT_SCHEMA = "history/v1"

# Directories whose contents change without Agent edits (test runs, imports,
# VCS internals) and would make every resume look like workspace drift.
_FINGERPRINT_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "node_modules", ".tox", ".eggs",
})
_FINGERPRINT_SKIP_SUFFIXES = (".pyc", ".pyo")
# About 21 µs per file on local disk, so the cap bounds the per-step cost
# (~0.4 s); typical repository-task workspaces have 10^2–10^4 files.
FINGERPRINT_MAX_FILES = 20_000


def system_prompt_sha256(system: str) -> str:
    return hashlib.sha256(system.encode("utf-8")).hexdigest()


def encode_history_snapshot(history: Any, system: str) -> bytes:
    payload = {
        "schema": HISTORY_SNAPSHOT_SCHEMA,
        "system_sha256": system_prompt_sha256(system),
        "messages": history.messages,
        "message_tokens": [list(pair) for pair in history.message_tokens],
        "total_tokens": history.total_tokens,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return zlib.compress(raw.encode("utf-8"), level=6)


def decode_history_snapshot(blob: bytes | None) -> dict[str, Any] | None:
    """Decode a snapshot; None for missing, corrupt or unknown-schema payloads."""
    if not blob:
        return None
    try:
        payload = json.loads(zlib.decompress(blob).decode("utf-8"))
    except (zlib.error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != HISTORY_SNAPSHOT_SCHEMA:
        return None
    return payload


def history_is_api_consistent(messages: list[dict[str, Any]]) -> bool:
    """True when every assistant tool call is answered by a tool result before the next turn."""
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "tool":
            pending.discard(str(message.get("tool_call_id", "")))
            continue
        if pending:
            return False
        if role == "assistant":
            pending = {str(call.get("id", "")) for call in message.get("tool_calls") or []}
    return not pending


def restore_history(history: Any, snapshot: dict[str, Any]) -> None:
    history.messages = list(snapshot["messages"])
    history.message_tokens = [tuple(pair) for pair in snapshot["message_tokens"]]
    history.total_tokens = int(snapshot["total_tokens"])


def workspace_fingerprint(root: Path, *, max_files: int = FINGERPRINT_MAX_FILES) -> str | None:
    """Hash of (relative path, size, mtime_ns) for workspace files.

    Metadata, not content, keeps this cheap enough to run every step. It is a
    drift detector: a false "changed" only adds a resume notice, never
    discards state. Returns None (unknown) when the workspace is missing or
    has more than ``max_files`` files; resume treats unknown as possible drift.
    """
    root = Path(root)
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _FINGERPRINT_SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(_FINGERPRINT_SKIP_SUFFIXES):
                continue
            seen += 1
            if seen > max_files:
                return None
            path = Path(dirpath) / name
            try:
                stat = path.lstat()
            except OSError:
                continue
            relative = path.relative_to(root).as_posix()
            digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()
