"""Shell execution tool with safety guardrails."""

import asyncio

from coder_agent.config import cfg
from coder_agent.tools.base import Tool

_WORKSPACE = cfg.agent.workspace
BLOCKED_PATTERNS: list[str] = cfg.tools.blocked_commands


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

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(_WORKSPACE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("command timed out")
        return f"Exit code: {proc.returncode}\nSTDOUT:\n{stdout.decode()}\nSTDERR:\n{stderr.decode()}"
