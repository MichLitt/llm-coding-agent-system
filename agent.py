"""Coder-Agent: ReAct coding agent with pluggable LLM backend.

Run modes
---------
    python agent.py "Add type hints to utils.py"   # single task
    python agent.py                                 # interactive chat (stdin loop)

Config (env vars / .env file)
------------------------------
    CODER_MODEL         Model ID passed to the LLM backend
    CODER_MAX_TOKENS    Max tokens per response (default: 8192)
    CODER_MAX_STEPS     Max loop iterations (default: 20)
    CODER_WORKSPACE     Workspace directory (default: ./workspace)
    CODER_VERBOSE       Print tool calls (default: false)

LLM backend
-----------
    The Agent accepts any callable `llm_client` that follows the interface
    defined in utils/llm_client.py. Swap backends via config without
    changing agent logic.
"""

import asyncio
import dataclasses
import json
import re
import sys
from dataclasses import dataclass
from typing import Any


import config
from tools.base import Tool
from utils.history_util import MessageHistory
from utils.tool_util import execute_tools
from utils.memory_manager import MemoryManager

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""\
You are an expert software engineering assistant.

You have access to tools that let you read, write, and execute files inside
the workspace directory ({config.WORKSPACE}).

Path rules (IMPORTANT):
- All file/directory paths must be RELATIVE to the workspace root.
- Use "hello.py", not "workspace/hello.py" or "{config.WORKSPACE}/hello.py".
- The workspace root is already your current directory — do not add any prefix.
- list_dir(".") lists the workspace root.

Self-correction rules:
- After running code, always check the exit code.
- If exit code != 0, analyze the stderr, fix the bug, and retry.
- Maximum {config.MAX_RETRIES} retries per file before giving up.

Guidelines:
- Think before acting: reason about what you need to do before calling a tool.
- Prefer small, targeted edits over full rewrites.
- After writing or editing code, run it (or run tests) to verify correctness.
- If a command fails, read the error carefully and fix the root cause.
- When the task is complete, summarise what you did and what changed.

Never access paths outside the workspace directory.
"""

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    model: str = config.MODEL
    max_tokens: int = config.MAX_TOKENS
    temperature: float = 1.0
    context_window_tokens: int = 180_000

@dataclass
class TurnResult:
    """Result of one agent turn, for display and trajectory analysis."""
    content: str           # final response text (think tags stripped)
    steps: int             # number of ReAct steps taken
    tool_calls: list[str]  # tool names called, in order
    success: bool          # False if max_steps was hit

class Agent:
    """ReAct loop: Reason → Act (tool call) → Observe → repeat."""

    def __init__(
        self,
        tools: list[Tool],
        system: str = SYSTEM_PROMPT,
        model_config: ModelConfig | None = None,
        verbose: bool = config.VERBOSE,
        client: Any | None = None,
        memory: MemoryManager | None = None,
    ):
        cfg = model_config or ModelConfig()
        self.tools = tools
        self.tool_dict = {t.name: t for t in tools}
        self.system = system
        self.config = cfg
        self.verbose = verbose
        self.client = client
        self.memory = memory
        self.history = MessageHistory(
            model=cfg.model,
            system=system,
            context_window_tokens=cfg.context_window_tokens,
            client=client,
        )

    def _params(self) -> dict[str, Any]:
        """Build kwargs for client.messages.create()."""
        return {f.name: getattr(self.config, f.name)
                for f in dataclasses.fields(self.config)
                if f.name != "context_window_tokens"}

    async def _loop(self, user_input: str) -> TurnResult:
        """Run one task to completion; return a TurnResult."""
        await self.history.add_message("user", user_input)
        steps = 0
        all_tool_calls: list[str] = []

        # Memory: register project and optionally inject recent task history
        _project_id: str | None = None
        if self.memory:
            _project_id = self.memory.get_or_create_project(config.WORKSPACE)
            recent = self.memory.get_recent_tasks(_project_id, n=3)
            if recent:
                summary_lines = ["Recent tasks in this project:"]
                for t in recent:
                    status = "✓" if t["success"] else "✗"
                    summary_lines.append(f"  {status} {t['description']} ({t['steps']} steps)")
                await self.history.add_message("user", "\n".join(summary_lines))

        # Streaming state: buffer to detect and suppress <think> blocks
        _in_think = False
        _think_buf = ""

        async def on_token(token: str) -> None:
            nonlocal _in_think, _think_buf
            _think_buf += token
            # Suppress everything inside <think>...</think>
            while True:
                if _in_think:
                    end = _think_buf.find("</think>")
                    if end == -1:
                        _think_buf = _think_buf[-len("</think>"):]  # keep tail for partial match
                        return
                    _in_think = False
                    _think_buf = _think_buf[end + len("</think>"):]
                else:
                    start = _think_buf.find("<think>")
                    if start == -1:
                        print(_think_buf, end="", flush=True)
                        _think_buf = ""
                        return
                    print(_think_buf[:start], end="", flush=True)
                    _in_think = True
                    _think_buf = _think_buf[start + len("<think>"):]

        for _ in range(config.MAX_STEPS):
            steps += 1
            _in_think = False
            _think_buf = ""
            self.history.truncate()
            params = self._params()
            response = await self.client.chat(
                messages=self.history.format_for_api(),
                system=self.system,
                tools=[t.to_dict() for t in self.tools],
                on_token=on_token,
                **params,
            )
            tool_uses = response.get("tool_uses", [])
            all_tool_calls.extend(tu["name"] for tu in tool_uses)
            if not tool_uses:
                print()  # newline after streamed final response
                text = " ".join(
                    b["text"] for b in response.get("content", []) if b.get("type") == "text"
                )
                result = TurnResult(
                    content=re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip(),
                    steps=steps,
                    tool_calls=all_tool_calls,
                    success=True,
                )
                if self.memory and _project_id:
                    self.memory.record_task(_project_id, user_input, result)
                return result
            print()  # newline after any streamed reasoning before tool call
            for tu in tool_uses:
                args_preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in tu["input"].items())
                print(f"  > {tu['name']}({args_preview})", flush=True)
            if self.verbose:
                pass  # already printed above

            # Build OpenAI-format tool_calls for the assistant message
            openai_tool_calls = [
                {
                    "id": tu["id"],
                    "type": "function",
                    "function": {"name": tu["name"], "arguments": json.dumps(tu["input"])},
                }
                for tu in tool_uses
            ]
            text_content = " ".join(
                b["text"] for b in response.get("content", []) if b.get("type") == "text"
            )
            await self.history.add_message(
                "assistant", text_content, tool_calls=openai_tool_calls
            )

            tool_results = await execute_tools(tool_uses, self.tool_dict)
            for tr in tool_results:
                status = "ERR" if tr.get("is_error") else "ok"
                preview = str(tr.get("content", "")).split("\n")[0][:80]
                print(f"    {status}: {preview}", flush=True)
            # Each tool result is its own message in OpenAI format
            for tr in tool_results:
                await self.history.add_message(
                    "tool",
                    tr.get("content", ""),
                    tool_calls=[{"id": tr["tool_use_id"]}],
                )
        result = TurnResult(
            content="Error: max steps reached",
            steps=steps,
            tool_calls=all_tool_calls,
            success=False,
        )
        if self.memory and _project_id:
            self.memory.record_task(_project_id, user_input, result)
        return result

    def run(self, user_input: str) -> TurnResult:
        return asyncio.run(self._loop(user_input))

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_tools() -> list[Tool]:
    """Instantiate all available tools."""
    from tools.file_tools import ReadFileTool, WriteFileTool, ListDirTool
    from tools.shell_tool import RunCommandTool
    from tools.search_tool import SearchCodeTool
    return [
        ReadFileTool(),
        WriteFileTool(),
        ListDirTool(),
        RunCommandTool(),
        SearchCodeTool(),
    ]


def main() -> None:
    from utils.llm_client import LLMClient
    memory = MemoryManager(config.MEMORY_DB_PATH) if config.MEMORY_ENABLED else None
    agent = Agent(tools=build_tools(), client=LLMClient(), memory=memory)

    if len(sys.argv) > 1:
        # Single task mode: python agent.py "do something"
        task = " ".join(sys.argv[1:])
        agent.run(task)
    else:
        # Interactive chat mode
        print("Coder-Agent ready. Type 'exit' to quit.")
        while True:
            try:
                task = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if task.lower() in ("exit", "quit"):
                break
            if task:
                agent.run(task)


if __name__ == "__main__":
    main()
