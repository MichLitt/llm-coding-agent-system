"""Shell execution tool with safety guardrails.

Runs a shell command inside the workspace directory and returns its stdout,
stderr, and exit code. Dangerous commands are blocked before execution.

Safety model
------------
- A static blocklist rejects obviously destructive patterns
  (e.g. "rm -rf /", "sudo", ":(){ :|:& };:").
- The working directory is always the workspace root (never /tmp, ~, etc.).
- A per-command timeout prevents runaway processes.

Tool
----
RunCommandTool — Execute a shell command and capture its output.
"""

import asyncio

import config
from .base import Tool


BLOCKED_PATTERNS: list[str] = [
    "rm -rf /",
    "rm -rf ~",
    "sudo",
    ":(){ :|:& };:",   # fork bomb
    "> /dev/sd",        # disk wipe
    "mkfs",
    "dd if=",
    "chmod -R 777 /",
]


class RunCommandTool(Tool):
    """Run a shell command inside the workspace.

    Input schema parameters
    -----------------------
    command : str
        The shell command to execute.
    timeout : int, optional
        Seconds before the subprocess is killed. Default: 30.

    Returns a formatted string containing stdout, stderr, and exit_code.
    On timeout or blocked command, returns an error string.
    """

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
            return "Error: command blocked for safety"
        
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(config.WORKSPACE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "Error: command timed out"
        return f"Exit code: {proc.returncode}\nSTDOUT:\n{stdout.decode()}\nSTDERR:\n{stderr.decode()}"
