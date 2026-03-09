"""Coder-Agent core loop."""

import asyncio
import dataclasses
import inspect
import json
import re
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from coder_agent.config import cfg
from coder_agent.core.context import MessageHistory
from coder_agent.memory.trajectory import Step, TrajectoryStore
from coder_agent.tools.base import Tool
from coder_agent.tools.execute import execute_tools


TERMINATION_MODEL_STOP = "model_stop"
TERMINATION_TOOL_NONZERO_EXIT = "tool_nonzero_exit"
TERMINATION_TOOL_EXCEPTION = "tool_exception"
TERMINATION_RETRY_EXHAUSTED = "retry_exhausted"
TERMINATION_LOOP_EXCEPTION = "loop_exception"
TERMINATION_MAX_STEPS = "max_steps"
TERMINATION_VERIFICATION_FAILED = "verification_failed"


def classify_error(stderr: str) -> str | None:
    """Classify the error type from stderr output."""
    if not stderr.strip():
        return None
    if "SyntaxError" in stderr:
        return "SyntaxError"
    if "ImportError" in stderr or "ModuleNotFoundError" in stderr:
        return "ImportError"
    if "AssertionError" in stderr:
        return "AssertionError"
    if "TimeoutError" in stderr or "timed out" in stderr.lower():
        return "TimeoutError"
    if "Traceback" in stderr or "Error" in stderr:
        return "LogicError"
    return None


def extract_exit_code(content: str) -> int | None:
    """Extract a shell exit code from tool output."""
    match = re.search(r"^Exit code:\s*(-?\d+)", content, flags=re.MULTILINE)
    if match is None:
        return None
    return int(match.group(1))


def extract_stderr(content: str) -> str:
    """Extract the STDERR section from run_command output."""
    if "STDERR:" not in content:
        return ""
    return content.split("STDERR:", maxsplit=1)[-1].strip()


_ERROR_GUIDANCE = {
    "SyntaxError": "There is a syntax error. Rewrite the specific function or block with the error - check brackets, indentation, and colons.",
    "ImportError": "A module is missing. First run `pip install <package>` via run_command, then retry.",
    "AssertionError": "An assertion failed. Read the test file to understand expected behavior, then fix the implementation logic.",
    "TimeoutError": "The code timed out. Reconsider the algorithm complexity - look for an O(n log n) or better approach.",
    "LogicError": "There is a logic error. Add debug print statements to trace variable values, analyze the traceback carefully, then fix the root cause.",
}


@dataclass
class ModelConfig:
    model: str = cfg.model.name
    max_tokens: int = cfg.model.max_tokens
    temperature: float = cfg.model.temperature
    context_window_tokens: int = cfg.context.context_window_tokens


@dataclass
class TurnResult:
    content: str
    steps: int
    tool_calls: list[str]
    success: bool
    retry_steps: int = 0
    total_tokens: int = 0
    trajectory_id: str | None = None
    final_status: str = "failed"
    termination_reason: str | None = None
    error_details: list[str] = dataclasses.field(default_factory=list)


@dataclass
class VerificationResult:
    passed: bool
    summary: str = ""


VerificationHook = Callable[[], VerificationResult | Awaitable[VerificationResult]]


def _build_system_prompt(
    planning_mode: str = "react",
    enable_correction: bool = True,
    max_retries: int | None = None,
    workspace: str | None = None,
) -> str:
    workspace = workspace or str(cfg.agent.workspace)
    max_retries = max_retries if max_retries is not None else cfg.agent.max_retries

    if planning_mode == "direct":
        planning_instruction = (
            "Generate the complete solution directly. "
            "You may use tools to read existing files or run code, "
            "but avoid lengthy step-by-step exploration — go straight to writing and verifying the solution."
        )
    else:
        planning_instruction = (
            "Think step by step before each action. "
            "Reason about what you need to do, then call the appropriate tool."
        )

    if enable_correction:
        correction_section = f"""\
Self-correction rules:
- After running code, always check the exit code.
- If exit code != 0, analyze the stderr carefully and apply the appropriate fix:
  * SyntaxError -> rewrite the specific function/block with the error
  * ImportError -> install the missing package first, then retry
  * AssertionError -> read the test file to understand expected behavior, then fix
  * TimeoutError -> reconsider algorithm complexity
  * Logic error -> add debug prints, trace the issue, fix the root cause
- Maximum {max_retries} retries per file before giving up and reporting the failure.

"""
    else:
        correction_section = ""

    return f"""\
You are an expert software engineering assistant.

You have access to tools that let you read, write, and execute files inside
the workspace directory ({workspace}).

Path rules (IMPORTANT):
- All file/directory paths must be RELATIVE to the workspace root.
- Use "hello.py", not "workspace/hello.py" or "{workspace}/hello.py".
- The workspace root is already your current directory - do not add any prefix.
- list_dir(".") lists the workspace root.

{correction_section}\
Guidelines:
- {planning_instruction}
- Prefer small, targeted edits over full rewrites.
- After writing or editing code, run it (or run tests) to verify correctness.
- If a command fails, read the error carefully and fix the root cause.
- When ALL required tasks are done and verified (tests pass, files created, etc.),
  stop calling tools and respond with a final summary only. Do NOT keep calling
  tools after the task is complete.

Never access paths outside the workspace directory.
"""


SYSTEM_PROMPT = _build_system_prompt()


class Agent:
    """ReAct loop: reason -> act -> observe -> repeat."""

    def __init__(
        self,
        tools: list[Tool],
        system: str | None = None,
        model_config: ModelConfig | None = None,
        verbose: bool = cfg.agent.verbose,
        client: Any | None = None,
        memory: Any | None = None,
        trajectory_store: TrajectoryStore | None = None,
        experiment_id: str = "default",
        experiment_config: dict | None = None,
    ):
        self._model_cfg = model_config or ModelConfig()
        self.tools = tools
        self.tool_dict = {tool.name: tool for tool in tools}
        self.experiment_id = experiment_id
        self.experiment_config = experiment_config or {}
        self.verbose = verbose
        self.client = client
        self.memory = memory
        self.trajectory_store = trajectory_store

        # Build system prompt from experiment_config if not explicitly provided
        if system is not None:
            self.system = system
        else:
            planning_mode = self.experiment_config.get("planning_mode", cfg.agent.planning_mode)
            enable_correction = self.experiment_config.get("correction", cfg.agent.enable_correction)
            self.system = _build_system_prompt(
                planning_mode=planning_mode,
                enable_correction=enable_correction,
            )

        self.history = MessageHistory(
            model=self._model_cfg.model,
            system=self.system,
            context_window_tokens=self._model_cfg.context_window_tokens,
            client=client,
        )

        # C5: Adaptive Checklist (Decomposer role)
        enable_checklist = self.experiment_config.get("checklist", cfg.agent.enable_checklist)
        if enable_checklist:
            from coder_agent.core.decomposer import Decomposer
            self.decomposer: Any = Decomposer()
        else:
            self.decomposer = None

    def _params(self) -> dict[str, Any]:
        return {
            field.name: getattr(self._model_cfg, field.name)
            for field in dataclasses.fields(self._model_cfg)
            if field.name != "context_window_tokens"
        }

    def reset(self) -> None:
        """Clear per-task conversation state while reusing the same client and tools."""
        # Rebuild system prompt in case experiment_config changed between tasks
        planning_mode = self.experiment_config.get("planning_mode", cfg.agent.planning_mode)
        enable_correction = self.experiment_config.get("correction", cfg.agent.enable_correction)
        self.system = _build_system_prompt(
            planning_mode=planning_mode,
            enable_correction=enable_correction,
        )
        self.history = MessageHistory(
            model=self._model_cfg.model,
            system=self.system,
            context_window_tokens=self._model_cfg.context_window_tokens,
            client=self.client,
        )
        # Reset decomposer state for new task
        if self.decomposer is not None:
            from coder_agent.core.decomposer import Decomposer
            self.decomposer = Decomposer()

    def _make_result(
        self,
        *,
        content: str,
        steps: int,
        tool_calls: list[str],
        success: bool,
        retry_steps: int,
        total_tokens: int,
        trajectory_id: str | None,
        final_status: str,
        termination_reason: str | None,
        error_details: list[str] | None = None,
    ) -> TurnResult:
        return TurnResult(
            content=content,
            steps=steps,
            tool_calls=tool_calls,
            success=success,
            retry_steps=retry_steps,
            total_tokens=total_tokens,
            trajectory_id=trajectory_id,
            final_status=final_status,
            termination_reason=termination_reason,
            error_details=error_details or [],
        )

    def _safe_print(self, text: str = "", end: str = "\n") -> None:
        """Print text without crashing on console encoding issues."""
        try:
            print(text, end=end, flush=True)
        except (UnicodeEncodeError, OSError):
            stdout = sys.stdout
            encoding = getattr(stdout, "encoding", None) or "utf-8"
            safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            try:
                print(safe_text, end=end, flush=True)
            except OSError:
                # Streaming output should never take down the agent loop.
                return

    async def _loop(
        self,
        user_input: str,
        task_id: str = "",
        finalize_trajectory: bool = True,
        verification_hook: VerificationHook | None = None,
        max_verification_attempts: int = 2,
    ) -> TurnResult:
        start_time = time.time()
        steps = 0
        all_tool_calls: list[str] = []
        retry_count = 0
        retry_steps = 0
        last_error_type: str | None = None
        project_id: str | None = None
        traj_id: str | None = None
        verification_attempts = 0
        exception_stage = "init"

        exception_stage = "history.add_user"
        await self.history.add_message("user", user_input)

        # C5: Decomposer generates the Adaptive Checklist before the first step
        if self.decomposer is not None:
            exception_stage = "decomposer.decompose"
            self._safe_print("\n[Decomposer] Generating task checklist...")
            goals = await self.decomposer.decompose(user_input, self.client)
            if goals:
                checklist_intro = "I've broken down this task into sub-goals:\n" + "\n".join(
                    f"  [{i}] {g}" for i, g in enumerate(goals, 1)
                )
                self._safe_print(checklist_intro)
                await self.history.add_message("user", checklist_intro)

        if self.memory:
            exception_stage = "memory.lookup"
            project_id = self.memory.get_or_create_project(cfg.agent.workspace)
            recent = self.memory.get_recent_tasks(project_id, n=3)
            if recent:
                summary_lines = ["Recent tasks in this project:"]
                for task in recent:
                    status = "OK" if task["success"] else "ERR"
                    summary_lines.append(
                        f"  {status} {task['description']} ({task['steps']} steps)"
                    )
                await self.history.add_message("user", "\n".join(summary_lines))

        if self.trajectory_store:
            traj_id = self.trajectory_store.start_trajectory(
                task_id=task_id or user_input[:40],
                experiment_id=self.experiment_id,
                config=self.experiment_config,
            )

        in_think = False
        think_buf = ""

        async def on_token(token: str) -> None:
            nonlocal in_think, think_buf
            think_buf += token
            while True:
                if in_think:
                    end = think_buf.find("</think>")
                    if end == -1:
                        think_buf = think_buf[-len("</think>") :]
                        return
                    in_think = False
                    think_buf = think_buf[end + len("</think>") :]
                else:
                    start = think_buf.find("<think>")
                    if start == -1:
                        self._safe_print(think_buf, end="")
                        think_buf = ""
                        return
                    self._safe_print(think_buf[:start], end="")
                    in_think = True
                    think_buf = think_buf[start + len("<think>") :]

        try:
            for _ in range(cfg.agent.max_steps):
                steps += 1
                step_start = time.time()
                in_think = False
                think_buf = ""
                self.history.truncate()

                # C5: inject progress prompt before each LLM call
                if self.decomposer is not None and steps > 1:
                    # Update completion state based on recent steps
                    recorded_steps = []
                    if self.trajectory_store and traj_id:
                        # Build lightweight step dicts from trajectory
                        pass  # update uses history observations instead
                    self.decomposer.update(
                        [{"observation": msg.get("content", "")} for msg in self.history.messages[-6:]]
                    )
                    progress = self.decomposer.to_progress_prompt()
                    if progress:
                        await self.history.add_message("user", progress)

                exception_stage = "llm.chat"
                response = await self.client.chat(
                    messages=self.history.format_for_api(),
                    system=self.system,
                    tools=[tool.to_dict() for tool in self.tools],
                    on_token=on_token,
                    **self._params(),
                )
                tool_uses = response.get("tool_uses", [])
                all_tool_calls.extend(tool_use["name"] for tool_use in tool_uses)

                if not tool_uses:
                    self._safe_print()
                    text = " ".join(
                        block["text"]
                        for block in response.get("content", [])
                        if block.get("type") == "text"
                    )
                    clean_text = re.sub(
                        r"<think>.*?</think>",
                        "",
                        text,
                        flags=re.DOTALL,
                    ).strip()

                    if verification_hook is not None:
                        verification_attempts += 1
                        exception_stage = "verification_hook"
                        verification_result = verification_hook()
                        if inspect.isawaitable(verification_result):
                            verification_result = await verification_result

                        if not verification_result.passed:
                            failure_summary = (
                                verification_result.summary.strip() or "Verification failed."
                            )
                            if traj_id:
                                self.trajectory_store.record_step(
                                    traj_id,
                                    Step(
                                        step_id=steps,
                                        thought=clean_text,
                                        action=None,
                                        observation=f"[verification failed]\n{failure_summary}"[:500],
                                        timestamp=step_start,
                                        error_type="VerificationFailed",
                                        is_retry=False,
                                    ),
                                )

                            if verification_attempts >= max_verification_attempts:
                                if traj_id and finalize_trajectory:
                                    self.trajectory_store.finish_trajectory(
                                        traj_id,
                                        final_status="failed",
                                        termination_reason=TERMINATION_VERIFICATION_FAILED,
                                        total_tokens=self.history.total_tokens,
                                        duration=time.time() - start_time,
                                    )
                                result = self._make_result(
                                    content=failure_summary,
                                    steps=steps,
                                    tool_calls=all_tool_calls,
                                    success=False,
                                    retry_steps=retry_steps,
                                    total_tokens=self.history.total_tokens,
                                    trajectory_id=traj_id,
                                    final_status="failed",
                                    termination_reason=TERMINATION_VERIFICATION_FAILED,
                                    error_details=[failure_summary],
                                )
                                if finalize_trajectory and self.memory and project_id:
                                    self.memory.record_task(project_id, user_input, result)
                                return result

                            exception_stage = "history.add_verification_feedback"
                            await self.history.add_message("assistant", clean_text)
                            await self.history.add_message(
                                "user",
                                (
                                    "External verification failed. Fix the implementation and only "
                                    "stop after verification passes.\n\n"
                                    f"{failure_summary}"
                                ),
                            )
                            continue

                    if traj_id:
                        self.trajectory_store.record_step(
                            traj_id,
                            Step(
                                step_id=steps,
                                thought=clean_text,
                                action=None,
                                observation="[task complete]",
                                timestamp=step_start,
                            ),
                        )
                        if finalize_trajectory:
                            self.trajectory_store.finish_trajectory(
                                traj_id,
                                final_status="success",
                                termination_reason=TERMINATION_MODEL_STOP,
                                partial_score=1.0,
                                total_tokens=self.history.total_tokens,
                                duration=time.time() - start_time,
                            )

                    result = TurnResult(
                        content=clean_text,
                        steps=steps,
                        tool_calls=all_tool_calls,
                        success=True,
                        retry_steps=retry_steps,
                        total_tokens=self.history.total_tokens,
                        trajectory_id=traj_id,
                        final_status="success",
                        termination_reason=TERMINATION_MODEL_STOP,
                        error_details=[],
                    )
                    if finalize_trajectory and self.memory and project_id:
                        self.memory.record_task(project_id, user_input, result)
                    return result

                self._safe_print()
                for tool_use in tool_uses:
                    args_preview = ", ".join(
                        f"{key}={repr(value)[:40]}"
                        for key, value in tool_use["input"].items()
                    )
                    self._safe_print(f"  > {tool_use['name']}({args_preview})")

                openai_tool_calls = [
                    {
                        "id": tool_use["id"],
                        "type": "function",
                        "function": {
                            "name": tool_use["name"],
                            "arguments": json.dumps(tool_use["input"]),
                        },
                    }
                    for tool_use in tool_uses
                ]
                text_content = " ".join(
                    block["text"]
                    for block in response.get("content", [])
                    if block.get("type") == "text"
                )
                await self.history.add_message(
                    "assistant",
                    text_content,
                    tool_calls=openai_tool_calls,
                )

                exception_stage = "tools.execute"
                tool_results = await execute_tools(tool_uses, self.tool_dict)
                for tool_result in tool_results:
                    status = "ERR" if tool_result.get("is_error") else "ok"
                    preview = str(tool_result.get("content", "")).split("\n")[0][:80]
                    self._safe_print(f"    {status}: {preview}")

                combined_observation = "\n---\n".join(
                    tool_result.get("content", "") for tool_result in tool_results
                )
                tool_exception = next(
                    (tool_result for tool_result in tool_results if tool_result.get("is_error")),
                    None,
                )
                if tool_exception is not None:
                    if traj_id:
                        action_dict = {
                            "tool": tool_uses[0]["name"],
                            "args": tool_uses[0]["input"],
                        } if tool_uses else None
                        self.trajectory_store.record_step(
                            traj_id,
                            Step(
                                step_id=steps,
                                thought=text_content,
                                action=action_dict,
                                observation=combined_observation[:500],
                                timestamp=step_start,
                                error_type="ToolError",
                                is_retry=False,
                            ),
                        )
                    if traj_id and finalize_trajectory:
                        self.trajectory_store.finish_trajectory(
                            traj_id,
                            final_status="failed",
                            termination_reason=TERMINATION_TOOL_EXCEPTION,
                            total_tokens=self.history.total_tokens,
                            duration=time.time() - start_time,
                        )
                    result = self._make_result(
                        content=str(tool_exception.get("content", "Error: tool execution failed")),
                        steps=steps,
                        tool_calls=all_tool_calls,
                        success=False,
                        retry_steps=retry_steps,
                        total_tokens=self.history.total_tokens,
                        trajectory_id=traj_id,
                        final_status="failed",
                        termination_reason=TERMINATION_TOOL_EXCEPTION,
                        error_details=[str(tool_exception.get("content", "Error: tool execution failed"))],
                    )
                    if finalize_trajectory and self.memory and project_id:
                        self.memory.record_task(project_id, user_input, result)
                    return result

                stderr_parts: list[str] = []
                saw_nonzero_exit = False
                for tool_result in tool_results:
                    content = str(tool_result.get("content", ""))
                    exit_code = extract_exit_code(content)
                    if exit_code is not None and exit_code != 0:
                        saw_nonzero_exit = True
                        stderr = extract_stderr(content)
                        if stderr:
                            stderr_parts.append(stderr)

                stderr_text = "\n".join(stderr_parts)
                detected_error = classify_error(stderr_text)
                if saw_nonzero_exit and detected_error is None:
                    detected_error = "LogicError"
                correction_enabled = self.experiment_config.get(
                    "correction", cfg.agent.enable_correction
                )
                if saw_nonzero_exit:
                    retry_steps += 1
                if saw_nonzero_exit and not correction_enabled:
                    if traj_id:
                        action_dict = {
                            "tool": tool_uses[0]["name"],
                            "args": tool_uses[0]["input"],
                        } if tool_uses else None
                        self.trajectory_store.record_step(
                            traj_id,
                            Step(
                                step_id=steps,
                                thought=text_content,
                                action=action_dict,
                                observation=combined_observation[:500],
                                timestamp=step_start,
                                error_type=detected_error,
                                is_retry=False,
                            ),
                        )
                    if traj_id and finalize_trajectory:
                        self.trajectory_store.finish_trajectory(
                            traj_id,
                            final_status="failed",
                            termination_reason=TERMINATION_TOOL_NONZERO_EXIT,
                            total_tokens=self.history.total_tokens,
                            duration=time.time() - start_time,
                        )
                    result = self._make_result(
                        content=combined_observation,
                        steps=steps,
                        tool_calls=all_tool_calls,
                        success=False,
                        retry_steps=retry_steps,
                        total_tokens=self.history.total_tokens,
                        trajectory_id=traj_id,
                        final_status="failed",
                        termination_reason=TERMINATION_TOOL_NONZERO_EXIT,
                        error_details=stderr_parts,
                    )
                    if finalize_trajectory and self.memory and project_id:
                        self.memory.record_task(project_id, user_input, result)
                    return result

                if correction_enabled and saw_nonzero_exit:
                    retry_count += 1
                    guidance = _ERROR_GUIDANCE.get(detected_error, "")
                    if guidance and detected_error != last_error_type and retry_count <= cfg.agent.max_retries:
                        combined_observation += (
                            f"\n\n[Self-correction hint - {detected_error}]: {guidance}"
                        )
                    last_error_type = detected_error

                if retry_count > cfg.agent.max_retries:
                    if traj_id and finalize_trajectory:
                        self.trajectory_store.finish_trajectory(
                            traj_id,
                            final_status="failed",
                            termination_reason=TERMINATION_RETRY_EXHAUSTED,
                            total_tokens=self.history.total_tokens,
                            duration=time.time() - start_time,
                        )
                    result = self._make_result(
                        content=f"Error: max retries ({cfg.agent.max_retries}) exceeded for {last_error_type}",
                        steps=steps,
                        tool_calls=all_tool_calls,
                        success=False,
                        retry_steps=retry_steps,
                        total_tokens=self.history.total_tokens,
                        trajectory_id=traj_id,
                        final_status="failed",
                        termination_reason=TERMINATION_RETRY_EXHAUSTED,
                        error_details=[f"Error: max retries ({cfg.agent.max_retries}) exceeded for {last_error_type}"],
                    )
                    if finalize_trajectory and self.memory and project_id:
                        self.memory.record_task(project_id, user_input, result)
                    return result

                if traj_id:
                    action_dict = None
                    if tool_uses:
                        action_dict = {
                            "tool": tool_uses[0]["name"],
                            "args": tool_uses[0]["input"],
                        }
                    self.trajectory_store.record_step(
                        traj_id,
                        Step(
                            step_id=steps,
                            thought=text_content,
                            action=action_dict,
                            observation=combined_observation[:500],
                            timestamp=step_start,
                            error_type=detected_error if saw_nonzero_exit else None,
                            is_retry=saw_nonzero_exit,
                        ),
                    )

                for tool_result in tool_results:
                    exception_stage = "history.add_tool"
                    await self.history.add_message(
                        "tool",
                        tool_result.get("content", ""),
                        tool_calls=[{"id": tool_result["tool_use_id"]}],
                    )

        except Exception as exc:
            tb_summary = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            error_summary = (
                f"Exception stage: {exception_stage}\n"
                f"Exception class: {type(exc).__name__}\n"
                f"Message: {exc}\n"
                f"Traceback:\n{tb_summary[:1200]}"
            )
            if traj_id:
                self.trajectory_store.record_step(
                    traj_id,
                    Step(
                        step_id=max(steps, 1),
                        thought="[system] unhandled exception in agent loop",
                        action=None,
                        observation=error_summary[:500],
                        timestamp=time.time(),
                        error_type=type(exc).__name__,
                        is_retry=False,
                    ),
                )
            if traj_id and finalize_trajectory:
                self.trajectory_store.finish_trajectory(
                    traj_id,
                    final_status="failed",
                    termination_reason=TERMINATION_LOOP_EXCEPTION,
                    total_tokens=self.history.total_tokens,
                    duration=time.time() - start_time,
                )
            result = self._make_result(
                content=error_summary,
                steps=steps,
                tool_calls=all_tool_calls,
                success=False,
                retry_steps=retry_steps,
                total_tokens=self.history.total_tokens,
                trajectory_id=traj_id,
                final_status="failed",
                termination_reason=TERMINATION_LOOP_EXCEPTION,
                error_details=[error_summary],
            )
            if finalize_trajectory and self.memory and project_id:
                self.memory.record_task(project_id, user_input, result)
            return result

        if traj_id and finalize_trajectory:
            self.trajectory_store.finish_trajectory(
                traj_id,
                final_status="timeout",
                termination_reason=TERMINATION_MAX_STEPS,
                total_tokens=self.history.total_tokens,
                duration=time.time() - start_time,
            )
        result = self._make_result(
            content="Error: max steps reached",
            steps=steps,
            tool_calls=all_tool_calls,
            success=False,
            retry_steps=retry_steps,
            total_tokens=self.history.total_tokens,
            trajectory_id=traj_id,
            final_status="timeout",
            termination_reason=TERMINATION_MAX_STEPS,
            error_details=["Error: max steps reached"],
        )
        if finalize_trajectory and self.memory and project_id:
            self.memory.record_task(project_id, user_input, result)
        return result

    def run(
        self,
        user_input: str,
        task_id: str = "",
        finalize_trajectory: bool = True,
        verification_hook: VerificationHook | None = None,
        max_verification_attempts: int = 2,
    ) -> TurnResult:
        return asyncio.run(
            self._loop(
                user_input,
                task_id=task_id,
                finalize_trajectory=finalize_trajectory,
                verification_hook=verification_hook,
                max_verification_attempts=max_verification_attempts,
            )
        )


def build_tools() -> list[Tool]:
    from coder_agent.tools.file_tools import ListDirTool, ReadFileTool, WriteFileTool
    from coder_agent.tools.search_tool import SearchCodeTool
    from coder_agent.tools.shell_tool import RunCommandTool

    return [
        ReadFileTool(),
        WriteFileTool(),
        ListDirTool(),
        RunCommandTool(),
        SearchCodeTool(),
    ]
