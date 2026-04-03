"""Shell execution tool with safety guardrails."""

import asyncio
import locale
import os
import re
import signal
import subprocess
import sys

from coder_agent.config import cfg
from coder_agent.tools.base import Tool

_WORKSPACE = cfg.agent.workspace
BLOCKED_PATTERNS: list[str] = cfg.tools.blocked_commands


def _decode_output(data: bytes) -> str:
    for encoding in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate a timed-out shell command and any child processes."""
    if proc.returncode is not None:
        return

    if sys.platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


class RunCommandTool(Tool):
    def __init__(self):
        super().__init__(
            name="run_command",
            description="Run a shell command inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["command"],
            },
        )

    async def execute(self, command: str, timeout: int = 30) -> str:
        if any(p in command for p in BLOCKED_PATTERNS):
            raise RuntimeError("command blocked for safety")

        # Normalize python/pytest invocations to the venv interpreter so that
        # "python foo.py" and "cd /path && python foo.py" work on systems where
        # only "python3" is in PATH (e.g. macOS).  The negative lookbehind
        # (?<![/.\w]) prevents matching path components like /usr/bin/python or
        # the token python3.
        #
        # A single-pass alternation regex is used to avoid the interaction bug
        # that occurs with sequential substitutions:
        #   python -m pytest  →(python)→  "/venv/python" -m pytest
        #                     →(pytest)→  "/venv/python" -m "/venv/python" -m pytest ← BUG
        # By using one regex with alternation (longest match first), each token
        # is replaced exactly once and the replacement text is never re-scanned.
        _exe = sys.executable

        def _replace_python_token(m: re.Match) -> str:
            token = m.group(0)
            if re.match(r"python\s+-m\s+pytest", token):
                return f'"{_exe}" -m pytest'
            if token == "pytest":
                return f'"{_exe}" -m pytest'
            return f'"{_exe}"'  # bare "python"

        command = re.sub(
            r'(?<![/.\w])(?:python\s+-m\s+pytest|python|pytest)(?=\s|$)',
            _replace_python_token,
            command,
        )

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(_WORKSPACE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await _terminate_process_tree(proc)
            raise RuntimeError("command timed out")
        return (
            f"Exit code: {proc.returncode}\n"
            f"STDOUT:\n{_decode_output(stdout)}\n"
            f"STDERR:\n{_decode_output(stderr)}"
        )
