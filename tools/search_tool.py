"""Code search tool (grep/ripgrep).

Searches for a regex pattern across files in the workspace and returns
the matching lines with file paths and line numbers, similar to `grep -rn`.

Tool
----
SearchCodeTool — Search for a pattern in workspace files.
"""

import asyncio
import re
import shutil

import config
from .base import Tool


class SearchCodeTool(Tool):
    """Search for a regex pattern across workspace files.

    Input schema parameters
    -----------------------
    pattern : str
        Regex pattern to search for.
    path : str, optional
        Sub-path within the workspace to restrict the search. Default: "." (all files).
    file_glob : str, optional
        Glob pattern to filter files, e.g. "*.py". Default: "*" (all files).
    case_sensitive : bool, optional
        Whether the match is case-sensitive. Default: True.
    max_results : int, optional
        Maximum number of matching lines to return. Default: 50.

    Returns matching lines formatted as "path:lineno: line_content".
    """

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

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        file_glob: str = "*",
        case_sensitive: bool = True,
        max_results: int = 50,
    ) -> str:
        if max_results < 1:
            return "Error: max_results must be >= 1"

        root = (config.WORKSPACE / path).resolve()
        if not root.is_relative_to(config.WORKSPACE):
            return "Error: path escapes workspace"
        if not root.exists():
            return f"Error: path not found: {path}"

        rel_path = str(root.relative_to(config.WORKSPACE))

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
                    cwd=str(config.WORKSPACE),
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

        files = [root] if root.is_file() else list(root.rglob(file_glob or "*"))
        results: list[str] = []
        for file_path in files:
            if not file_path.is_file():
                continue
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel_file = file_path.relative_to(config.WORKSPACE)
                            results.append(f"{rel_file}:{lineno}: {line.rstrip()}")
                            if len(results) >= max_results:
                                return "\n".join(results)
            except OSError:
                continue

        if not results:
            return "No matches found."
        return "\n".join(results)
