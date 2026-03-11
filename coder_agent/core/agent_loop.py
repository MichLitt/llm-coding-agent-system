import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from coder_agent.config import cfg
from coder_agent.core.agent_run_context import (
    add_decomposer_progress,
    finalize_turn,
    record_trajectory_step,
    seed_run_context,
    start_trajectory,
)
from coder_agent.core.agent_tool_batch import (
    apply_retry_guidance,
    handle_tool_messages,
    handle_verification_auto_complete,
    summarize_tool_batch,
)
from coder_agent.core.agent_turns import (
    add_assistant_tool_call_message,
    build_action_dict,
    check_retry_edit_policy,
    handle_completion_turn,
    handle_parse_only_turn,
    handle_retry_edit_policy_violation,
    parse_model_turn,
    print_tool_call_preview,
)
from coder_agent.core.agent_types import (
    TERMINATION_LOOP_EXCEPTION,
    TERMINATION_MAX_STEPS,
    TERMINATION_RETRY_EXHAUSTED,
    TERMINATION_TOOL_EXCEPTION,
    TERMINATION_TOOL_NONZERO_EXIT,
    TurnResult,
    VerificationHook,
)


@dataclass
class LoopState:
    start_time: float = field(default_factory=time.time)
    steps: int = 0
    all_tool_calls: list[str] = field(default_factory=list)
    retry_count: int = 0
    retry_steps: int = 0
    last_error_type: str | None = None
    last_error_signature: str | None = None
    project_id: str | None = None
    traj_id: str | None = None
    verification_attempts: int = 0
    exception_stage: str = "init"
    awaiting_retry_verification: bool = False
    retry_edit_target: str | None = None


@dataclass
class ModelTurn:
    text_content: str
    tool_uses: list[dict[str, Any]]
    parse_errors: list[str]
    parse_feedback: str


@dataclass
class ToolBatchSummary:
    tool_results: list[dict[str, Any]]
    combined_observation: str
    hard_tool_exception: dict[str, Any] | None
    tool_error_messages: list[str]
    saw_recoverable_tool_error: bool
    saw_nonzero_exit: bool
    failure_parts: list[str]
    detected_error: str | None


class _TokenPrinter:
    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.in_think = False
        self.think_buf = ""

    def reset(self) -> None:
        self.in_think = False
        self.think_buf = ""

    async def on_token(self, token: str) -> None:
        self.think_buf += token
        while True:
            if self.in_think:
                end = self.think_buf.find("</think>")
                if end == -1:
                    self.think_buf = self.think_buf[-len("</think>") :]
                    return
                self.in_think = False
                self.think_buf = self.think_buf[end + len("</think>") :]
            else:
                start = self.think_buf.find("<think>")
                if start == -1:
                    self.agent._safe_print(self.think_buf, end="")
                    self.think_buf = ""
                    return
                self.agent._safe_print(self.think_buf[:start], end="")
                self.in_think = True
                self.think_buf = self.think_buf[start + len("<think>") :]


async def run_agent_loop(
    agent: Any,
    user_input: str,
    *,
    task_id: str = "",
    finalize_trajectory: bool = True,
    verification_hook: VerificationHook | None = None,
    max_verification_attempts: int = 2,
    enforce_stop_verification: bool = True,
    auto_complete_on_verification: bool = False,
    execute_tools_fn: Callable[[list[dict[str, Any]], dict[str, Any]], Awaitable[list[dict[str, Any]]]],
) -> TurnResult:
    state = LoopState()
    token_printer = _TokenPrinter(agent)

    await seed_run_context(agent, state, user_input)
    start_trajectory(agent, state, user_input=user_input, task_id=task_id)

    try:
        for _ in range(cfg.agent.max_steps):
            state.steps += 1
            step_start = time.time()
            token_printer.reset()
            agent.history.truncate()

            await add_decomposer_progress(agent, state)

            state.exception_stage = "llm.chat"
            response = await agent.client.chat(
                messages=agent.history.format_for_api(),
                system=agent.system,
                tools=[tool.to_dict() for tool in agent.tools],
                on_token=token_printer.on_token,
                **agent._params(),
            )
            turn = parse_model_turn(response, model_turn_cls=ModelTurn)
            state.all_tool_calls.extend(tool_use["name"] for tool_use in turn.tool_uses)

            if turn.parse_errors and not turn.tool_uses:
                await handle_parse_only_turn(agent, state, turn, step_start=step_start)
                continue

            if not turn.tool_uses:
                completion_result = await handle_completion_turn(
                    agent,
                    state,
                    turn,
                    user_input=user_input,
                    finalize_trajectory=finalize_trajectory,
                    verification_hook=verification_hook,
                    max_verification_attempts=max_verification_attempts,
                    enforce_stop_verification=enforce_stop_verification,
                    step_start=step_start,
                )
                if completion_result is not None:
                    return completion_result
                continue

            retry_policy_feedback = check_retry_edit_policy(state, turn.tool_uses)
            if retry_policy_feedback:
                await handle_retry_edit_policy_violation(
                    agent,
                    state,
                    turn,
                    feedback=retry_policy_feedback,
                    step_start=step_start,
                )
                continue

            print_tool_call_preview(agent, turn.tool_uses)
            await add_assistant_tool_call_message(
                agent,
                text_content=turn.text_content,
                tool_uses=turn.tool_uses,
            )

            state.exception_stage = "tools.execute"
            tool_results = await execute_tools_fn(turn.tool_uses, agent.tool_dict)
            for tool_result in tool_results:
                status = "ERR" if tool_result.get("is_error") else "ok"
                preview = str(tool_result.get("content", "")).split("\n")[0][:80]
                agent._safe_print(f"    {status}: {preview}")

            batch = summarize_tool_batch(
                tool_results,
                parse_errors=turn.parse_errors,
                summary_cls=ToolBatchSummary,
            )
            if any(tool_use["name"] == "run_command" for tool_use in turn.tool_uses):
                state.awaiting_retry_verification = False
                state.retry_edit_target = None

            if batch.hard_tool_exception is not None:
                record_trajectory_step(
                    agent,
                    state,
                    thought=turn.text_content,
                    action=build_action_dict(turn.tool_uses),
                    observation=batch.combined_observation,
                    timestamp=step_start,
                    error_type="ToolError",
                    is_retry=False,
                )
                return finalize_turn(
                    agent,
                    state,
                    user_input=user_input,
                    finalize_trajectory=finalize_trajectory,
                    content=str(batch.hard_tool_exception.get("content", "Error: tool execution failed")),
                    success=False,
                    final_status="failed",
                    termination_reason=TERMINATION_TOOL_EXCEPTION,
                    error_details=[str(batch.hard_tool_exception.get("content", "Error: tool execution failed"))],
                )

            combined_observation, correction_feedback = apply_retry_guidance(agent, state, batch)

            verification_result = await handle_verification_auto_complete(
                agent,
                state,
                user_input=user_input,
                finalize_trajectory=finalize_trajectory,
                verification_hook=verification_hook,
                auto_complete_on_verification=auto_complete_on_verification,
                turn=turn,
                batch=batch,
                combined_observation=combined_observation,
                step_start=step_start,
            )
            if verification_result is not None:
                return verification_result

            correction_enabled = agent.experiment_config.get("correction", cfg.agent.enable_correction)
            if batch.saw_nonzero_exit and not correction_enabled:
                record_trajectory_step(
                    agent,
                    state,
                    thought=turn.text_content,
                    action=build_action_dict(turn.tool_uses),
                    observation=combined_observation,
                    timestamp=step_start,
                    error_type=batch.detected_error,
                    is_retry=False,
                )
                return finalize_turn(
                    agent,
                    state,
                    user_input=user_input,
                    finalize_trajectory=finalize_trajectory,
                    content=combined_observation,
                    success=False,
                    final_status="failed",
                    termination_reason=TERMINATION_TOOL_NONZERO_EXIT,
                    error_details=batch.failure_parts,
                )

            if state.retry_count > cfg.agent.max_retries:
                return finalize_turn(
                    agent,
                    state,
                    user_input=user_input,
                    finalize_trajectory=finalize_trajectory,
                    content=f"Error: max retries ({cfg.agent.max_retries}) exceeded for {state.last_error_type}",
                    success=False,
                    final_status="failed",
                    termination_reason=TERMINATION_RETRY_EXHAUSTED,
                    error_details=[
                        f"Error: max retries ({cfg.agent.max_retries}) exceeded for {state.last_error_type}"
                    ],
                )

            record_trajectory_step(
                agent,
                state,
                thought=turn.text_content,
                action=build_action_dict(turn.tool_uses),
                observation=combined_observation,
                timestamp=step_start,
                error_type=(
                    batch.detected_error
                    if batch.saw_nonzero_exit or batch.saw_recoverable_tool_error
                    else ("ToolCallParseError" if turn.parse_errors else None)
                ),
                is_retry=batch.saw_nonzero_exit or batch.saw_recoverable_tool_error,
            )
            await handle_tool_messages(
                agent,
                state,
                batch=batch,
                parse_feedback=turn.parse_feedback,
                correction_feedback=correction_feedback,
            )

    except Exception as exc:
        tb_summary = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        error_summary = (
            f"Exception stage: {state.exception_stage}\n"
            f"Exception class: {type(exc).__name__}\n"
            f"Message: {exc}\n"
            f"Traceback:\n{tb_summary[:1200]}"
        )
        record_trajectory_step(
            agent,
            state,
            thought="[system] unhandled exception in agent loop",
            action=None,
            observation=error_summary,
            timestamp=time.time(),
            error_type=type(exc).__name__,
            is_retry=False,
        )
        return finalize_turn(
            agent,
            state,
            user_input=user_input,
            finalize_trajectory=finalize_trajectory,
            content=error_summary,
            success=False,
            final_status="failed",
            termination_reason=TERMINATION_LOOP_EXCEPTION,
            error_details=[error_summary],
        )

    return finalize_turn(
        agent,
        state,
        user_input=user_input,
        finalize_trajectory=finalize_trajectory,
        content="Error: max steps reached",
        success=False,
        final_status="timeout",
        termination_reason=TERMINATION_MAX_STEPS,
        error_details=["Error: max steps reached"],
    )
