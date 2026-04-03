"""Code search tool (grep/ripgrep)."""

import asyncio
import re
import shutil
from pathlib import PurePosixPath, PureWindowsPath

from coder_agent.config import cfg
from coder_agent.tools.base import Tool

_WORKSPACE = cfg.agent.workspace


def _validate_file_glob(file_glob: str) -> str | None:
    if not file_glob or file_glob == "*":
        return None
    if PurePosixPath(file_glob).is_absolute() or PureWindowsPath(file_glob).is_absolute():
        return (
            "Error: file_glob must be a relative glob pattern (e.g. '*.py'), "
            f"not an absolute path. Got: {file_glob!r}. "
            "Use the path parameter to narrow the search directory instead."
        )
    return None


class SearchCodeTool(Tool):
    def __init__(self):
        super().__init__(
            name="search_code",
            description="Search for a regex pattern across workspace files.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "file_glob": {"type": "string", "default": "*"},
                    "case_sensitive": {"type": "boolean", "default": True},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": ["pattern"],
            },
        )

    async def execute(self, pattern: str, path: str = ".", file_glob: str = "*", case_sensitive: bool = True, max_results: int = 50) -> str:
        if max_results < 1:
            return "Error: max_results must be >= 1"

        root = (_WORKSPACE / path).resolve()
        if not root.is_relative_to(_WORKSPACE):
            return "Error: path escapes workspace"
        if not root.exists():
            return f"Error: path not found: {path}"
        file_glob_error = _validate_file_glob(file_glob)
        if file_glob_error:
            return file_glob_error

        rel_path = str(root.relative_to(_WORKSPACE))

        rg_path = shutil.which("rg")
        if rg_path:
            args = [rg_path, "--line-number", "--no-heading", "--color", "never"]
            if not case_sensitive:
                args.append("-i")
            if file_glob and file_glob != "*":
                args.extend(["-g", file_glob])
            args.extend(["-e", pattern, rel_path])
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    cwd=str(_WORKSPACE),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
            except Exception as e:
                return f"Error: {e}"

            if proc.returncode not in (0, 1):
                message = stderr.decode("utf-8", errors="replace").strip()
                return f"Error: {message or 'ripgrep failed'}"

            lines = stdout.decode("utf-8", errors="replace").splitlines()
            if not lines:
                return "No matches found."
            return "\n".join(lines[:max_results])

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        try:
            files = [root] if root.is_file() else list(root.rglob(file_glob or "*"))
        except NotImplementedError:
            return (
                "Error: file_glob must be a relative glob pattern (e.g. '*.py'), "
                f"not an absolute path. Got: {file_glob!r}. "
                "Use the path parameter to narrow the search directory instead."
            )
        results: list[str] = []
        for file_path in files:
            if not file_path.is_file():
                continue
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel_file = file_path.relative_to(_WORKSPACE)
                            results.append(f"{rel_file}:{lineno}: {line.rstrip()}")
                            if len(results) >= max_results:
                                return "\n".join(results)
            except OSError:
                continue

        if not results:
            return "No matches found."
        return "\n".join(results)
