"""File system tools: read, write/edit, and list directory.

All file paths are validated against the workspace root to prevent the
agent from escaping the sandbox.

Tools
-----
ReadFileTool   — Read a file's contents (with optional line limit).
WriteFileTool  — Create or overwrite a file; or make a targeted text edit.
ListDirTool    — List files and subdirectories with optional depth control.
"""

from pathlib import Path

import config
from .base import Tool


def _safe_path(path: str) -> Path | None:
    """Resolve path and verify it stays inside the workspace.

    Accepts relative paths (resolved against workspace root) or absolute paths
    that already point inside the workspace. Returns None if the path escapes
    the sandbox.
    """
    p = Path(path)
    full = p.resolve() if p.is_absolute() else (config.WORKSPACE / path).resolve()
    if not full.is_relative_to(config.WORKSPACE):
        return None
    return full


class ReadFileTool(Tool):
    """Read a file from the workspace.

    Input schema parameters
    -----------------------
    path : str
        Path to the file (relative to workspace root).
    max_lines : int, optional
        Maximum number of lines to return. 0 = no limit (default).
    """

    def __init__(self):
        super().__init__(
            name="read_file",
            description="Read a file from the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_lines": {"type": "integer", "default": 0},
                },
                "required": ["path"],
            },
        )

    async def execute(self, path: str, max_lines: int = 0) -> str:
        full_path = _safe_path(path)
        if full_path is None:
            return "Error: path escapes workspace"
        if not full_path.is_file():
            return f"Error: file not found: {path}"
        try:
            with full_path.open("r", encoding="utf-8", errors="replace") as f:
                if max_lines > 0:
                    lines = []
                    for i, line in enumerate(f):
                        if i >= max_lines:
                            break
                        lines.append(line)
                    return "".join(lines)
                return f.read()
        except Exception as e:
            return f"Error: {e}"


class WriteFileTool(Tool):
    """Create/overwrite a file, or perform a targeted text replacement.

    Input schema parameters
    -----------------------
    operation : "write" | "edit"
        * write — create or fully overwrite the file with `content`.
        * edit  — replace the first occurrence of `old_text` with `new_text`.
    path : str
        Target file path (relative to workspace root).
    content : str, optional
        Full file content for the "write" operation.
    old_text : str, optional
        Exact text to find for the "edit" operation.
    new_text : str, optional
        Replacement text for the "edit" operation.
    """

    def __init__(self):
        super().__init__(
            name="write_file",
            description="Create/overwrite a file, or perform a targeted text replacement.",
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["write", "edit"]},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["operation", "path"],
            },
        )

    async def execute(
        self,
        operation: str,
        path: str,
        content: str = "",
        old_text: str = "",
        new_text: str = "",
    ) -> str:
        full_path = _safe_path(path)
        if full_path is None:
            return "Error: path escapes workspace"
        if operation == "write":
            return self._write(full_path, content)
        elif operation == "edit":
            return self._edit(full_path, old_text, new_text)
        return f"Error: unknown operation '{operation}'"

    def _write(self, full_path: Path, content: str) -> str:
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            return f"Written: {full_path.relative_to(config.WORKSPACE)}"
        except Exception as e:
            return f"Error: {e}"

    def _edit(self, full_path: Path, old_text: str, new_text: str) -> str:
        if not full_path.is_file():
            return f"Error: file not found: {full_path.name}"
        if not old_text:
            return "Error: old_text is required for edit"
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            if old_text not in content:
                return "Error: old_text not found in file"
            full_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Edited: {full_path.relative_to(config.WORKSPACE)}"
        except Exception as e:
            return f"Error: {e}"


class ListDirTool(Tool):
    """List directory contents as a tree.

    Input schema parameters
    -----------------------
    path : str
        Directory path (relative to workspace root). Defaults to workspace root.
    depth : int, optional
        Maximum recursion depth. 1 = immediate children only (default).
    """

    def __init__(self):
        super().__init__(
            name="list_dir",
            description="List directory contents as a tree.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "depth": {"type": "integer", "default": 1},
                },
                "required": [],
            },
        )

    async def execute(self, path: str = ".", depth: int = 1) -> str:
        full_path = _safe_path(path)
        if full_path is None:
            return "Error: path escapes workspace"
        if not full_path.is_dir():
            return f"Error: directory not found: {path}"
        if depth < 1:
            return "Error: depth must be >= 1"

        root_label = f"[workspace root: {config.WORKSPACE}]" if path in ("", ".") else path.rstrip("/\\")
        lines = [root_label]
        stack = [(full_path, 0)]

        while stack:
            current_dir, current_depth = stack.pop()
            if current_depth >= depth:
                continue
            try:
                entries = sorted(current_dir.iterdir(), key=lambda e: (e.is_file(), e.name))
                prefix = "  " * (current_depth + 1)
                child_dirs = []
                for entry in entries:
                    is_dir = entry.is_dir()
                    lines.append(f"{prefix}{entry.name}{'/' if is_dir else ''}")
                    if is_dir:
                        child_dirs.append(entry)
                for child in reversed(child_dirs):
                    stack.append((child, current_depth + 1))
            except Exception as e:
                return f"Error: {e}"

        return "\n".join(lines)
