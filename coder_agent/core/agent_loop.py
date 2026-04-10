import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from coder_agent.config import cfg
from coder_agent.memory.run_state import RunMetrics, RunStateStore
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
    TERMINATION_CANCELLED,
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
    run_id: str | None = None
    steps: int = 0
    all_tool_calls: list[str] = field(default_factory=list)
    successful_tool_calls: int = 0
    retry_count: int = 0
    retry_steps: int = 0
    last_error_type: str | None = None
    last_error_signature: str | None = None
    project_id: str | None = None
    traj_id: str | None = None
    verification_attempts: int = 0
    verification_failures: int = 0
    verification_recovery_action_seen: bool = False
    verification_recovery_impl_paths: set[str] = field(default_factory=set)
    no_tool_completion_verification_failures: int = 0
    last_verification_failure_signature: str | None = None
    last_verification_failure_target: str | None = None
    consecutive_verification_failures: int = 0
    exception_stage: str = "init"
    recovery_mode: str = "none"
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
    ad_hoc_install_count: int = 0
    task_metadata: dict[str, Any] = field(default_factory=dict)
    resume_summary: str = ""


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


def _serialize_loop_state(state: LoopState) -> dict[str, Any]:
    return {
        "start_time": state.start_time,
        "steps": state.steps,
        "all_tool_calls": list(state.all_tool_calls),
        "successful_tool_calls": state.successful_tool_calls,
        "retry_count": state.retry_count,
        "retry_steps": state.retry_steps,
        "last_error_type": state.last_error_type,
        "last_error_signature": state.last_error_signature,
        "verification_attempts": state.verification_attempts,
        "verification_failures": state.verification_failures,
        "verification_recovery_action_seen": state.verification_recovery_action_seen,
        "verification_recovery_impl_paths": sorted(state.verification_recovery_impl_paths),
        "no_tool_completion_verification_failures": state.no_tool_completion_verification_failures,
        "last_verification_failure_signature": state.last_verification_failure_signature,
        "last_verification_failure_target": state.last_verification_failure_target,
        "consecutive_verification_failures": state.consecutive_verification_failures,
        "recovery_mode": state.recovery_mode,
        "retry_edit_target": state.retry_edit_target,
        "consecutive_identical_failures": state.consecutive_identical_failures,
        "last_failing_call_sig": state.last_failing_call_sig,
        "doom_loop_warnings_injected": state.doom_loop_warnings_injected,
        "observations_compressed": state.observations_compressed,
        "compaction_events": state.compaction_events,
        "tried_approaches": list(state.tried_approaches),
        "approach_memory_injections": state.approach_memory_injections,
        "cross_task_memory_injected": state.cross_task_memory_injected,
        "memory_injections": state.memory_injections,
        "db_records_written": state.db_records_written,
        "ad_hoc_install_count": state.ad_hoc_install_count,
        "resume_summary": state.resume_summary,
    }


def _restore_loop_state(resume_state: dict[str, Any] | None) -> LoopState:
    state = LoopState()
    if not resume_state:
        return state
    state.start_time = float(resume_state.get("run_started_at") or resume_state.get("start_time") or time.time())
    state.steps = int(resume_state.get("steps", state.steps))
    state.all_tool_calls = list(resume_state.get("all_tool_calls", state.all_tool_calls))
    state.successful_tool_calls = int(resume_state.get("successful_tool_calls", 0))
    state.retry_count = int(resume_state.get("retry_count", 0))
    state.retry_steps = int(resume_state.get("retry_steps", 0))
    state.last_error_type = resume_state.get("last_error_type")
    state.last_error_signature = resume_state.get("last_error_signature")
    state.verification_attempts = int(resume_state.get("verification_attempts", 0))
    state.verification_failures = int(resume_state.get("verification_failures", 0))
    state.verification_recovery_action_seen = bool(resume_state.get("verification_recovery_action_seen", False))
    state.verification_recovery_impl_paths = set(resume_state.get("verification_recovery_impl_paths", []))
    state.no_tool_completion_verification_failures = int(
        resume_state.get("no_tool_completion_verification_failures", 0)
    )
    state.last_verification_failure_signature = resume_state.get("last_verification_failure_signature")
    state.last_verification_failure_target = resume_state.get("last_verification_failure_target")
    state.consecutive_verification_failures = int(resume_state.get("consecutive_verification_failures", 0))
    state.recovery_mode = str(resume_state.get("recovery_mode", "none"))
    state.retry_edit_target = resume_state.get("retry_edit_target")
    state.consecutive_identical_failures = int(resume_state.get("consecutive_identical_failures", 0))
    state.last_failing_call_sig = resume_state.get("last_failing_call_sig")
    state.doom_loop_warnings_injected = int(resume_state.get("doom_loop_warnings_injected", 0))
    state.observations_compressed = int(resume_state.get("observations_compressed", 0))
    state.compaction_events = int(resume_state.get("compaction_events", 0))
    state.tried_approaches = list(resume_state.get("tried_approaches", []))
    state.approach_memory_injections = int(resume_state.get("approach_memory_injections", 0))
    state.cross_task_memory_injected = bool(resume_state.get("cross_task_memory_injected", False))
    state.memory_injections = int(resume_state.get("memory_injections", 0))
    state.db_records_written = int(resume_state.get("db_records_written", 0))
    state.ad_hoc_install_count = int(resume_state.get("ad_hoc_install_count", 0))
    state.resume_summary = str(resume_state.get("resume_summary", "") or "")
    return state


def _build_run_metrics(agent: Any, state: LoopState) -> RunMetrics:
    total_tool_calls = len(state.all_tool_calls)
    success_rate = None if total_tool_calls == 0 else state.successful_tool_calls / total_tool_calls
    return RunMetrics(
        total_steps=state.steps,
        total_tool_calls=total_tool_calls,
        total_tokens=agent.history.total_tokens,
        tool_success_rate=success_rate,
    )


def _record_tool_audit(
    store: RunStateStore | None,
    run_id: str | None,
    step_index: int,
    tool_uses: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> None:
    if store is None or not run_id:
        return
    tool_inputs = {str(tool_use.get("id", "")): tool_use for tool_use in tool_uses}
    for tool_result in tool_results:
        tool_use_id = str(tool_result.get("tool_use_id", "") or "")
        tool_input = tool_inputs.get(tool_use_id, {})
        store.record_tool_call(
            run_id,
            step_index,
            tool_use_id=tool_use_id,
            tool_name=str(tool_input.get("name") or "unknown"),
            args=tool_input.get("input", {}),
            result_text=str(tool_result.get("content", "")),
            is_error=bool(tool_result.get("is_error")),
            error_kind=str(tool_result.get("error_kind")) if tool_result.get("error_kind") else None,
            duration_ms=int(tool_result.get("duration_ms", 0) or 0),
        )


def _record_step_checkpoint(
    store: RunStateStore | None,
    run_id: str | None,
    state: LoopState,
    *,
    thought: str,
    observation: str,
    tool_call_count: int,
    had_error: bool,
    step_tokens: int,
    step_duration_ms: int,
) -> None:
    if store is None or not run_id:
        return
    store.record_step(
        run_id,
        max(0, state.steps - 1),
        thought=thought,
        observation=observation,
        tool_call_count=tool_call_count,
        had_error=had_error,
        step_tokens=step_tokens,
        step_duration_ms=step_duration_ms,
        loop_state=_serialize_loop_state(state),
    )


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


def _configure_task_tool_state(agent: Any, state: LoopState) -> None:
    max_installs = int(_runtime_setting(agent, "max_ad_hoc_installs_per_task", 1))
    run_command_tool = agent.tool_dict.get("run_command")
    if run_command_tool is not None and hasattr(run_command_tool, "configure_ad_hoc_install_budget"):
        run_command_tool.configure_ad_hoc_install_budget(max_installs)
    state.ad_hoc_install_count = 0


def _restore_task_tool_state(agent: Any, state: LoopState) -> None:
    run_command_tool = agent.tool_dict.get("run_command")
    if run_command_tool is not None and hasattr(run_command_tool, "ad_hoc_install_count"):
        run_command_tool.ad_hoc_install_count = int(getattr(state, "ad_hoc_install_count", 0))


def _sync_tool_state(agent: Any, state: LoopState) -> None:
    run_command_tool = agent.tool_dict.get("run_command")
    if run_command_tool is not None and hasattr(run_command_tool, "ad_hoc_install_count"):
        state.ad_hoc_install_count = int(getattr(run_command_tool, "ad_hoc_install_count", 0))


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
    task_metadata: dict[str, Any] | None = None,
    finalize_trajectory: bool = True,
    verification_hook: VerificationHook | None = None,
    max_verification_attempts: int = 2,
    enforce_stop_verification: bool = True,
    auto_complete_on_verification: bool = False,
    max_steps: int | None = None,
    execute_tools_fn: Callable[[list[dict[str, Any]], dict[str, Any]], Awaitable[list[dict[str, Any]]]],
    run_id: str | None = None,
    run_state_store: RunStateStore | None = None,
    resume_state: dict[str, Any] | None = None,
    cancel_event: Any | None = None,
) -> TurnResult:
    state = _restore_loop_state(resume_state)
    state.run_id = run_id
    state.task_metadata = dict(task_metadata or {})
    token_printer = _TokenPrinter(agent)
    effective_max_steps = max_steps if max_steps is not None else cfg.agent.max_steps

    def finalize_result(result: TurnResult) -> TurnResult:
        if run_state_store is not None and run_id:
            error_summary = "\n".join(result.error_details) if result.error_details else None
            run_state_store.finish_run(
                run_id,
                result.final_status,
                result.termination_reason,
                error_summary,
                _build_run_metrics(agent, state),
            )
            result.extra["run_id"] = run_id
        return _attach_activation_counters(result, state)

    _configure_task_tool_state(agent, state)
    if resume_state:
        _restore_task_tool_state(agent, state)
    if run_state_store is not None and run_id:
        run_state_store.start_run(run_id)
    await seed_run_context(agent, state, user_input)
    if state.resume_summary:
        state.exception_stage = "history.add_resume_summary"
        await agent.history.add_message("user", f"[Resume context]\n{state.resume_summary}")
    start_trajectory(agent, state, user_input=user_input, task_id=task_id)

    def cancel_requested() -> bool:
        return bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())

    def cancelled_result(message: str = "Run cancelled") -> TurnResult:
        return finalize_result(
            finalize_turn(
                agent,
                state,
                user_input=user_input,
                finalize_trajectory=finalize_trajectory,
                content=message,
                success=False,
                final_status="cancelled",
                termination_reason=TERMINATION_CANCELLED,
                error_details=[message],
            )
        )

    try:
        remaining_steps = max(0, effective_max_steps - state.steps)
        for _ in range(remaining_steps):
            if cancel_requested():
                return cancelled_result()
            state.steps += 1
            step_start = time.time()
            tokens_before_step = agent.history.total_tokens
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
                _record_step_checkpoint(
                    run_state_store,
                    run_id,
                    state,
                    thought=turn.text_content or "[tool call parse failure]",
                    observation=turn.parse_feedback,
                    tool_call_count=0,
                    had_error=True,
                    step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                    step_duration_ms=int((time.time() - step_start) * 1000),
                )
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
                completion_observation = ""
                if completion_result is not None:
                    completion_observation = completion_result.content
                    _record_step_checkpoint(
                        run_state_store,
                        run_id,
                        state,
                        thought=turn.text_content,
                        observation=completion_observation,
                        tool_call_count=0,
                        had_error=not completion_result.success,
                        step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                        step_duration_ms=int((time.time() - step_start) * 1000),
                    )
                    return finalize_result(completion_result)
                if agent.history.messages:
                    completion_observation = str(agent.history.messages[-1].get("content", ""))
                _record_step_checkpoint(
                    run_state_store,
                    run_id,
                    state,
                    thought=turn.text_content,
                    observation=completion_observation or turn.text_content,
                    tool_call_count=0,
                    had_error=False,
                    step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                    step_duration_ms=int((time.time() - step_start) * 1000),
                )
                continue

            retry_policy_feedback = check_retry_edit_policy(agent, state, turn.tool_uses)
            if retry_policy_feedback:
                await handle_retry_edit_policy_violation(
                    agent,
                    state,
                    turn,
                    feedback=retry_policy_feedback,
                    step_start=step_start,
                )
                _record_step_checkpoint(
                    run_state_store,
                    run_id,
                    state,
                    thought=turn.text_content or "[retry edit policy violation]",
                    observation=retry_policy_feedback,
                    tool_call_count=len(turn.tool_uses),
                    had_error=True,
                    step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                    step_duration_ms=int((time.time() - step_start) * 1000),
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
            if cancel_requested():
                _record_step_checkpoint(
                    run_state_store,
                    run_id,
                    state,
                    thought=turn.text_content,
                    observation="Run cancelled",
                    tool_call_count=len(turn.tool_uses),
                    had_error=True,
                    step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                    step_duration_ms=int((time.time() - step_start) * 1000),
                )
                return cancelled_result()
            _sync_tool_state(agent, state)
            state.successful_tool_calls += sum(1 for tool_result in tool_results if not tool_result.get("is_error"))
            _record_tool_audit(
                run_state_store,
                run_id,
                max(0, state.steps - 1),
                turn.tool_uses,
                tool_results,
            )
            for tool_result in tool_results:
                status = "ERR" if tool_result.get("is_error") else "ok"
                preview = str(tool_result.get("content", "")).split("\n")[0][:80]
                agent._safe_print(f"    {status}: {preview}")

            batch = summarize_tool_batch(
                tool_results,
                parse_errors=turn.parse_errors,
                summary_cls=ToolBatchSummary,
            )
            if any(tool_use["name"] in {"write_file", "patch_file", "run_command"} for tool_use in turn.tool_uses):
                state.verification_recovery_action_seen = True
            if any(tool_use["name"] == "run_command" for tool_use in turn.tool_uses):
                state.recovery_mode = "none"
                state.retry_edit_target = None
                state.verification_recovery_impl_paths = set()

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
                hard_error = str(batch.hard_tool_exception.get("content", "Error: tool execution failed"))
                _record_step_checkpoint(
                    run_state_store,
                    run_id,
                    state,
                    thought=turn.text_content,
                    observation=batch.combined_observation or hard_error,
                    tool_call_count=len(turn.tool_uses),
                    had_error=True,
                    step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                    step_duration_ms=int((time.time() - step_start) * 1000),
                )
                return finalize_result(
                    finalize_turn(
                        agent,
                        state,
                        user_input=user_input,
                        finalize_trajectory=finalize_trajectory,
                        content=hard_error,
                        success=False,
                        final_status="failed",
                        termination_reason=TERMINATION_TOOL_EXCEPTION,
                        error_details=[hard_error],
                    )
                )

            combined_observation, correction_feedback = apply_retry_guidance(agent, state, batch)
            if (
                state.recovery_mode == "verification"
                and not batch.saw_nonzero_exit
                and not batch.saw_recoverable_tool_error
            ):
                edited_impl_paths = {
                    str(tool_use["input"].get("path", "")).strip()
                    for tool_use in turn.tool_uses
                    if tool_use["name"] in {"write_file", "patch_file"}
                    and str(tool_use["input"].get("path", "")).strip()
                    and "/test" not in str(tool_use["input"].get("path", "")).replace("\\", "/")
                    and not str(tool_use["input"].get("path", "")).replace("\\", "/").startswith("tests/")
                    and not str(tool_use["input"].get("path", "")).split("/")[-1].startswith("test_")
                }
                state.verification_recovery_impl_paths.update(edited_impl_paths)
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
                _record_step_checkpoint(
                    run_state_store,
                    run_id,
                    state,
                    thought=turn.text_content,
                    observation=verification_result.content,
                    tool_call_count=len(turn.tool_uses),
                    had_error=False,
                    step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                    step_duration_ms=int((time.time() - step_start) * 1000),
                )
                return finalize_result(verification_result)

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
                _record_step_checkpoint(
                    run_state_store,
                    run_id,
                    state,
                    thought=turn.text_content,
                    observation=combined_observation,
                    tool_call_count=len(turn.tool_uses),
                    had_error=True,
                    step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                    step_duration_ms=int((time.time() - step_start) * 1000),
                )
                return finalize_result(
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
                    )
                )

            if state.retry_count > cfg.agent.max_retries:
                retry_message = f"Error: max retries ({cfg.agent.max_retries}) exceeded for {state.last_error_type}"
                _record_step_checkpoint(
                    run_state_store,
                    run_id,
                    state,
                    thought=turn.text_content,
                    observation=retry_message,
                    tool_call_count=len(turn.tool_uses),
                    had_error=True,
                    step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                    step_duration_ms=int((time.time() - step_start) * 1000),
                )
                return finalize_result(
                    finalize_turn(
                        agent,
                        state,
                        user_input=user_input,
                        finalize_trajectory=finalize_trajectory,
                        content=retry_message,
                        success=False,
                        final_status="failed",
                        termination_reason=TERMINATION_RETRY_EXHAUSTED,
                        error_details=[retry_message],
                    )
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
            _record_step_checkpoint(
                run_state_store,
                run_id,
                state,
                thought=turn.text_content,
                observation=combined_observation,
                tool_call_count=len(turn.tool_uses),
                had_error=batch.saw_nonzero_exit or batch.saw_recoverable_tool_error,
                step_tokens=max(0, agent.history.total_tokens - tokens_before_step),
                step_duration_ms=int((time.time() - step_start) * 1000),
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
        _record_step_checkpoint(
            run_state_store,
            run_id,
            state,
            thought="[system] unhandled exception in agent loop",
            observation=error_summary,
            tool_call_count=0,
            had_error=True,
            step_tokens=0,
            step_duration_ms=0,
        )
        return finalize_result(
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
            )
        )

    timeout_message = "Error: max steps reached"
    if remaining_steps == 0:
        timeout_message = "Error: max steps reached before resume could continue"
    return finalize_result(
        finalize_turn(
            agent,
            state,
            user_input=user_input,
            finalize_trajectory=finalize_trajectory,
            content=timeout_message,
            success=False,
            final_status="timeout",
            termination_reason=TERMINATION_MAX_STEPS,
            error_details=[timeout_message],
        )
    )
