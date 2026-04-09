from typing import Any

from coder_agent.config import cfg
from coder_agent.core.agent_errors import (
    classify_error,
    extract_combined_failure_text,
    extract_exit_code,
    extract_failure_excerpt,
)
from coder_agent.core.agent_run_context import finalize_turn, record_trajectory_step, run_verification_hook
from coder_agent.core.agent_turns import build_action_dict
from coder_agent.core.agent_types import TERMINATION_VERIFICATION_PASSED, TurnResult, VerificationHook


def summarize_tool_batch(
    tool_results: list[dict[str, Any]],
    *,
    parse_errors: list[str],
    summary_cls: type[Any],
) -> Any:
    combined_observation = "\n---\n".join(
        tool_result.get("content", "") for tool_result in tool_results
    )
    if parse_errors:
        warning = (
            "[tool-call parse warning]\n"
            "Malformed tool-call arguments were ignored and not executed.\n"
            + "\n".join(f"- {err}" for err in parse_errors[:3])
        )
        combined_observation = (
            f"{combined_observation}\n\n{warning}"
            if combined_observation
            else warning
        )

    hard_tool_exception = next(
        (
            tool_result
            for tool_result in tool_results
            if tool_result.get("is_error") and tool_result.get("error_kind") == "unknown_tool"
        ),
        None,
    )
    tool_error_messages = [
        str(tool_result.get("content", ""))
        for tool_result in tool_results
        if tool_result.get("is_error") and tool_result.get("error_kind") != "unknown_tool"
    ]

    failure_parts: list[str] = []
    saw_nonzero_exit = False
    for tool_result in tool_results:
        content = str(tool_result.get("content", ""))
        exit_code = extract_exit_code(content)
        if exit_code is None or exit_code == 0:
            continue
        saw_nonzero_exit = True
        failure_text = extract_combined_failure_text(content)
        if failure_text:
            failure_parts.append(failure_text)

    detected_error = classify_error("\n".join(failure_parts))
    if saw_nonzero_exit and detected_error is None:
        detected_error = "LogicError"
    saw_recoverable_tool_error = bool(tool_error_messages)
    if saw_recoverable_tool_error and detected_error is None:
        detected_error = "ToolError"

    return summary_cls(
        tool_results=tool_results,
        combined_observation=combined_observation,
        hard_tool_exception=hard_tool_exception,
        tool_error_messages=tool_error_messages,
        saw_recoverable_tool_error=saw_recoverable_tool_error,
        saw_nonzero_exit=saw_nonzero_exit,
        failure_parts=failure_parts,
        detected_error=detected_error,
    )


def apply_retry_guidance(agent: Any, state: Any, batch: Any) -> tuple[str, str]:
    combined_observation = batch.combined_observation
    if not batch.saw_nonzero_exit and not batch.saw_recoverable_tool_error:
        return combined_observation, ""

    state.retry_steps += 1
    state.retry_count += 1
    error_parts = [*batch.failure_parts, *batch.tool_error_messages]
    error_signature = f"{batch.detected_error}:{''.join(error_parts).strip()[:200]}"
    repeated_error = error_signature == state.last_error_signature
    correction_enabled = agent.experiment_config.get("correction", cfg.agent.enable_correction)
    failure_text = "\n".join(error_parts)
    guidance = agent._build_error_guidance(
        batch.detected_error,
        failure_text,
        repeated=repeated_error,
    )
    correction_feedback = ""
    if (
        correction_enabled
        and guidance
        and (batch.detected_error != state.last_error_type or repeated_error)
        and state.retry_count <= cfg.agent.max_retries
    ):
        combined_observation += f"\n\n[Self-correction hint - {batch.detected_error}]: {guidance}"
        correction_feedback = (
            f"Previous command failed ({batch.detected_error or 'unknown error'}). "
            "Before the next write, read the failing output and the relevant file(s). "
            f"{guidance}"
        )
        failure_excerpt = extract_failure_excerpt(failure_text)
        if failure_excerpt:
            correction_feedback += (
                "\n\nFocus on the first concrete failure before making broader changes:\n"
                f"{failure_excerpt}"
            )
    if correction_enabled:
        state.recovery_mode = "tool_error"
    state.last_error_type = batch.detected_error
    state.last_error_signature = error_signature
    return combined_observation, correction_feedback


async def handle_verification_auto_complete(
    agent: Any,
    state: Any,
    *,
    user_input: str,
    finalize_trajectory: bool,
    verification_hook: VerificationHook | None,
    auto_complete_on_verification: bool,
    turn: Any,
    batch: Any,
    combined_observation: str,
    step_start: float,
) -> TurnResult | None:
    if (
        not auto_complete_on_verification
        or verification_hook is None
        or batch.saw_nonzero_exit
        or batch.saw_recoverable_tool_error
        or not any(tool_use["name"] in {"write_file", "patch_file", "run_command"} for tool_use in turn.tool_uses)
    ):
        return None

    verification_result = await run_verification_hook(verification_hook, state=state)
    if not verification_result.passed:
        return None

    success_summary = verification_result.summary.strip() or "External verification passed."
    verification_note = f"[external verification passed]\n{success_summary}"
    success_observation = (
        f"{combined_observation}\n\n{verification_note}"
        if combined_observation else verification_note
    )
    record_trajectory_step(
        agent,
        state,
        thought=turn.text_content,
        action=build_action_dict(turn.tool_uses),
        observation=success_observation,
        timestamp=step_start,
        error_type=None,
        is_retry=False,
    )
    return finalize_turn(
        agent,
        state,
        user_input=user_input,
        finalize_trajectory=finalize_trajectory,
        content=success_summary,
        success=True,
        final_status="success",
        termination_reason=TERMINATION_VERIFICATION_PASSED,
        error_details=[],
        partial_score=1.0,
    )


async def handle_tool_messages(
    agent: Any,
    state: Any,
    *,
    batch: Any,
    parse_feedback: str,
    correction_feedback: str,
) -> None:
    for tool_result in batch.tool_results:
        state.exception_stage = "history.add_tool"
        await agent.history.add_message(
            "tool",
            tool_result.get("content", ""),
            tool_calls=[{"id": tool_result["tool_use_id"]}],
        )
    if parse_feedback:
        state.exception_stage = "history.add_parse_feedback"
        await agent.history.add_message("user", parse_feedback)
    if correction_feedback:
        state.exception_stage = "history.add_correction_feedback"
        await agent.history.add_message("user", correction_feedback)
