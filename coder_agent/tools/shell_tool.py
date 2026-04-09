"""Shell execution tool with safety guardrails."""

import asyncio
import locale
import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path

from coder_agent.config import cfg
from coder_agent.core.workspace_env import workspace_command_env, workspace_python_executable
from coder_agent.tools.base import Tool
from coder_agent.tools.command_budget import is_ad_hoc_install_command

BLOCKED_PATTERNS: list[str] = cfg.tools.blocked_commands


def _decode_output(data: bytes) -> str:
    for encoding in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _wrap_with_pipefail(command: str) -> str:
    if sys.platform.startswith("win") or "|" not in command:
        return command
    # Preserve the original shell command while ensuring pipeline exit codes
    # reflect upstream pytest/python failures instead of the final pipe stage.
    return f"bash -o pipefail -lc {shlex.quote(command)}"


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
    def __init__(self, workspace: Path | None = None):
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
        self.workspace = Path(workspace or cfg.agent.workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.max_ad_hoc_installs_per_task: int | None = None
        self.ad_hoc_install_count: int = 0

    def configure_ad_hoc_install_budget(self, max_installs: int | None) -> None:
        self.max_ad_hoc_installs_per_task = max_installs
        self.ad_hoc_install_count = 0

    def _budget_error(self) -> str:
        return (
            "Error: ad hoc install budget exceeded for this task. "
            "Do not keep retrying package installation. Check project-local imports, "
            "task-local virtualenv setup, and existing setup_commands first."
        )

    def try_reserve_ad_hoc_install(self, command: str) -> str | None:
        if not is_ad_hoc_install_command(command):
            return None
        if self.max_ad_hoc_installs_per_task is not None and (
            self.ad_hoc_install_count >= self.max_ad_hoc_installs_per_task
        ):
            return self._budget_error()
        self.ad_hoc_install_count += 1
        return None

    async def execute(
        self,
        command: str,
        timeout: int = 30,
        _install_budget_reserved: bool = False,
    ) -> str:
        if any(p in command for p in BLOCKED_PATTERNS):
            raise RuntimeError("command blocked for safety")
        if not _install_budget_reserved:
            budget_error = self.try_reserve_ad_hoc_install(command)
            if budget_error:
                return budget_error

        # Normalize python/pytest invocations to the workspace interpreter so that
        # "python foo.py", "python3 foo.py", and "cd /path && pytest" are bound
        # to the active task-local venv when present. The negative lookbehind
        # (?<![/.\w]) prevents matching path components like /usr/bin/python.
        #
        # A single-pass alternation regex is used to avoid the interaction bug
        # that occurs with sequential substitutions:
        #   python -m pytest  →(python)→  "/venv/python" -m pytest
        #                     →(pytest)→  "/venv/python" -m "/venv/python" -m pytest ← BUG
        # By using one regex with alternation (longest match first), each token
        # is replaced exactly once and the replacement text is never re-scanned.
        _exe = str(workspace_python_executable(self.workspace))

        def _replace_python_token(m: re.Match) -> str:
            token = m.group(0)
            if re.match(r"python\s+-m\s+pytest", token):
                return f'"{_exe}" -m pytest'
            if token == "pytest":
                return f'"{_exe}" -m pytest'
            return f'"{_exe}"'  # bare "python" / "python3"

        command = re.sub(
            r'(?<![/.\w])(?:python\s+-m\s+pytest|python3|python|pytest)(?=\s|$)',
            _replace_python_token,
            command,
        )
        command = _wrap_with_pipefail(command)

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=workspace_command_env(self.workspace),
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
