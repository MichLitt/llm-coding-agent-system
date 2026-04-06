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
from coder_agent.core.context import compress_observation
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
    consecutive_identical_failures: int = 0
    last_failing_call_sig: str | None = None
    doom_loop_warnings_injected: int = 0
    observations_compressed: int = 0
    compaction_events: int = 0
    tried_approaches: list[dict] = field(default_factory=list)
    approach_memory_injections: int = 0
    cross_task_memory_injected: bool = False
    memory_injections: int = 0
    db_records_written: int = 0


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


def _runtime_setting(agent: Any, key: str, default: Any) -> Any:
    return getattr(agent, "_experiment_config", {}).get(key, default)


def _tool_call_sig(tool_use: dict[str, Any]) -> str:
    args = tool_use.get("input", {})
    key_arg = args.get("cmd") or args.get("command") or args.get("path") or args.get("content", "")
    return f"{tool_use['name']}:{str(key_arg)[:80]}"


def _error_type_key(detected_error: str | None) -> str:
    if not detected_error:
        return "none"
    first_line = str(detected_error).split("\n", 1)[0]
    return first_line.split(":", 1)[0].strip()[:60] or "none"


def _attach_activation_counters(result: TurnResult, state: LoopState) -> TurnResult:
    result.extra["doom_loop_warnings_injected"] = state.doom_loop_warnings_injected
    result.extra["observations_compressed"] = state.observations_compressed
    result.extra["compaction_events"] = state.compaction_events
    result.extra["approach_memory_injections"] = state.approach_memory_injections
    result.extra["memory_injections"] = getattr(state, "memory_injections", 0)
    result.extra["db_records_written"] = getattr(state, "db_records_written", 0)
    return result


def _remove_messages_with_prefix(history: Any, prefix: str) -> None:
    kept_pairs = [
        (
            message,
            history.message_tokens[index] if index < len(history.message_tokens) else (0, 0),
        )
        for index, message in enumerate(history.messages)
        if not str(message.get("content", "")).startswith(prefix)
    ]
    history.messages = [message for message, _ in kept_pairs]
    history.message_tokens = [tokens for _, tokens in kept_pairs]


def _single_line_excerpt(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _inject_approach_memory(agent: Any, state: LoopState) -> None:
    sentinel = "[Memory/Approaches]"
    _remove_messages_with_prefix(agent.history, sentinel)

    lines = [f"{sentinel} Approaches already tried and failed in this task:"]
    max_entries = 2 if state.cross_task_memory_injected else 3
    max_chars = 400
    recent_approaches = state.tried_approaches[-max_entries:]
    for index, approach in enumerate(recent_approaches, start=1):
        tool_text = ", ".join(approach.get("tools", [])) or "unknown tools"
        error_text = _single_line_excerpt(approach.get("error") or "unknown error", limit=120)
        observation_head = _single_line_excerpt(approach.get("observation_head", ""), limit=80)
        lines.append(f"  {index}. {tool_text} -> {error_text}: {observation_head}")
    lines.append("Do not repeat these. Use a different approach.")
    injection = "\n".join(lines)
    if len(injection) > max_chars:
        injection = injection[: max_chars - 3].rstrip() + "..."

    agent.history.messages.insert(0, {"role": "user", "content": injection})
    agent.history.message_tokens.insert(0, (0, 0))


async def _maybe_compact_history(agent: Any, state: LoopState) -> None:
    msg_threshold = _runtime_setting(
        agent,
        "history_compaction_message_threshold",
        cfg.context.history_compaction_message_threshold,
    )
    compaction_mode = _runtime_setting(
        agent,
        "history_compaction_mode",
        cfg.context.history_compaction_mode,
    )
    if compaction_mode != "semantic" or len(agent.history.messages) <= msg_threshold:
        return

    keep_recent_turns = _runtime_setting(
        agent,
        "keep_recent_turns",
        cfg.context.keep_recent_turns,
    )

    state.exception_stage = "history.compact"
    await agent.history.compact(
        agent.client,
        agent._params(),
        keep_recent=keep_recent_turns,
    )
    state.exception_stage = None
    state.compaction_events += 1


def _count_compressed_observations(agent: Any, tool_results: list[dict[str, Any]]) -> int:
    return sum(
        1
        for tool_result in tool_results
        if compress_observation(
            str(tool_result.get("content", "")),
            getattr(agent, "_experiment_config", {}),
        ).was_compressed
    )


async def _update_failure_tracking(agent: Any, state: LoopState, turn: ModelTurn, batch: ToolBatchSummary) -> None:
    batch_has_error = batch.saw_nonzero_exit or batch.saw_recoverable_tool_error
    if not batch_has_error:
        state.consecutive_identical_failures = 0
        state.last_failing_call_sig = None
        return

    sig = f"{sorted(_tool_call_sig(tool_use) for tool_use in turn.tool_uses)}:{_error_type_key(batch.detected_error)}"
    if sig == state.last_failing_call_sig:
        state.consecutive_identical_failures += 1
    else:
        state.consecutive_identical_failures = 1
        state.last_failing_call_sig = sig

    threshold = _runtime_setting(agent, "doom_loop_threshold", cfg.agent.doom_loop_threshold)
    if threshold > 0 and state.consecutive_identical_failures == threshold:
        warning = (
            "[System] You have issued the same failing command "
            f"{state.consecutive_identical_failures} times in a row without progress. "
            "This approach is not working. Stop and try a fundamentally different strategy."
        )
        state.exception_stage = "history.add_doom_loop_warning"
        await agent.history.add_message("user", warning)
        state.doom_loop_warnings_injected += 1


def _remember_failed_approach(state: LoopState, turn: ModelTurn, batch: ToolBatchSummary, observation: str) -> None:
    if state.retry_count < 1:
        return
    state.tried_approaches.append(
        {
            "attempt": state.retry_count,
            "tools": [tool_use["name"] for tool_use in turn.tool_uses],
            "error": batch.detected_error,
            "observation_head": observation[:200],
        }
    )


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
    max_steps: int | None = None,
    execute_tools_fn: Callable[[list[dict[str, Any]], dict[str, Any]], Awaitable[list[dict[str, Any]]]],
) -> TurnResult:
    state = LoopState()
    token_printer = _TokenPrinter(agent)
    effective_max_steps = max_steps if max_steps is not None else cfg.agent.max_steps

    await seed_run_context(agent, state, user_input)
    start_trajectory(agent, state, user_input=user_input, task_id=task_id)

    try:
        for _ in range(effective_max_steps):
            state.steps += 1
            step_start = time.time()
            token_printer.reset()
            agent.history.truncate()
            await _maybe_compact_history(agent, state)

            if (
                len(state.tried_approaches) >= 2
                and _runtime_setting(agent, "enable_approach_memory", cfg.agent.enable_approach_memory)
                and agent.memory is not None
            ):
                _inject_approach_memory(agent, state)
                state.approach_memory_injections += 1

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
                    return _attach_activation_counters(completion_result, state)
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
                return _attach_activation_counters(
                    finalize_turn(
                        agent,
                        state,
                        user_input=user_input,
                        finalize_trajectory=finalize_trajectory,
                        content=str(batch.hard_tool_exception.get("content", "Error: tool execution failed")),
                        success=False,
                        final_status="failed",
                        termination_reason=TERMINATION_TOOL_EXCEPTION,
                        error_details=[str(batch.hard_tool_exception.get("content", "Error: tool execution failed"))],
                    ),
                    state,
                )

            combined_observation, correction_feedback = apply_retry_guidance(agent, state, batch)
            await _update_failure_tracking(agent, state, turn, batch)
            if batch.saw_nonzero_exit or batch.saw_recoverable_tool_error:
                _remember_failed_approach(state, turn, batch, combined_observation)
            state.observations_compressed += _count_compressed_observations(agent, batch.tool_results)

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
                return _attach_activation_counters(verification_result, state)

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
                return _attach_activation_counters(
                    finalize_turn(
                        agent,
                        state,
                        user_input=user_input,
                        finalize_trajectory=finalize_trajectory,
                        content=combined_observation,
                        success=False,
                        final_status="failed",
                        termination_reason=TERMINATION_TOOL_NONZERO_EXIT,
                        error_details=batch.failure_parts,
                    ),
                    state,
                )

            if state.retry_count > cfg.agent.max_retries:
                return _attach_activation_counters(
                    finalize_turn(
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
                    ),
                    state,
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
        return _attach_activation_counters(
            finalize_turn(
                agent,
                state,
                user_input=user_input,
                finalize_trajectory=finalize_trajectory,
                content=error_summary,
                success=False,
                final_status="failed",
                termination_reason=TERMINATION_LOOP_EXCEPTION,
                error_details=[error_summary],
            ),
            state,
        )

    return _attach_activation_counters(
        finalize_turn(
            agent,
            state,
            user_input=user_input,
            finalize_trajectory=finalize_trajectory,
            content="Error: max steps reached",
            success=False,
            final_status="timeout",
            termination_reason=TERMINATION_MAX_STEPS,
            error_details=["Error: max steps reached"],
        ),
        state,
    )
