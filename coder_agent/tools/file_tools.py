"""File system tools: read, write/edit, and list directory."""

from pathlib import Path
from typing import Any

from coder_agent.config import cfg
from coder_agent.tools.base import Tool

def _safe_path(workspace: Path, path: str) -> Path | None:
    p = Path(path)
    full = p.resolve() if p.is_absolute() else (workspace / path).resolve()
    if not full.is_relative_to(workspace):
        return None
    return full


class ReadFileTool(Tool):
    def __init__(self, workspace: Path | None = None):
        super().__init__(
            name="read_file",
            description="Read a file from the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "default": 1},
                    "max_lines": {"type": "integer", "default": 0},
                    "min_lines": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string"},
                        ]
                    },
                },
                "required": ["path"],
            },
        )
        self.workspace = Path(workspace or cfg.agent.workspace).resolve()

    def _normalize_start_line(
        self,
        *,
        start_line: int = 1,
        min_lines: Any = None,
    ) -> int:
        if start_line != 1:
            return max(1, int(start_line))
        if min_lines is None:
            return 1
        if isinstance(min_lines, str):
            min_lines = min_lines.strip()
            if not min_lines:
                return 1
            if min_lines.isdigit():
                return max(1, int(min_lines))
            raise ValueError("min_lines must be an integer or numeric string")
        return max(1, int(min_lines))

    async def execute(
        self,
        path: str,
        max_lines: int = 0,
        start_line: int = 1,
        min_lines: Any = None,
    ) -> str:
        full_path = _safe_path(self.workspace, path)
        if full_path is None:
            return "Error: path escapes workspace"
        if not full_path.is_file():
            return f"Error: file not found: {path}"
        try:
            start_line = self._normalize_start_line(start_line=start_line, min_lines=min_lines)
            with full_path.open("r", encoding="utf-8", errors="replace") as f:
                if start_line > 1 or max_lines > 0:
                    lines = []
                    for i, line in enumerate(f, start=1):
                        if i < start_line:
                            continue
                        if max_lines > 0 and len(lines) >= max_lines:
                            break
                        lines.append(line)
                    return "".join(lines)
                return f.read()
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"


class WriteFileTool(Tool):
    def __init__(self, workspace: Path | None = None):
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
        self.workspace = Path(workspace or cfg.agent.workspace).resolve()

    async def execute(self, operation: str, path: str, content: str = "", old_text: str = "", new_text: str = "") -> str:
        full_path = _safe_path(self.workspace, path)
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
            return f"Written: {full_path.relative_to(self.workspace)}"
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
            return f"Edited: {full_path.relative_to(self.workspace)}"
        except Exception as e:
            return f"Error: {e}"


class ListDirTool(Tool):
    def __init__(self, workspace: Path | None = None):
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
        self.workspace = Path(workspace or cfg.agent.workspace).resolve()

    async def execute(self, path: str = ".", depth: int = 1) -> str:
        full_path = _safe_path(self.workspace, path)
        if full_path is None:
            return "Error: path escapes workspace"
        if not full_path.is_dir():
            return f"Error: directory not found: {path}"
        if depth < 1:
            return "Error: depth must be >= 1"

        root_label = f"[workspace root: {self.workspace}]" if path in ("", ".") else path.rstrip("/\\")
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
