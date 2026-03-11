import json
import re
from typing import Any

from coder_agent.core.agent_run_context import finalize_turn, record_trajectory_step, run_verification_hook
from coder_agent.core.agent_types import (
    TERMINATION_MODEL_STOP,
    TERMINATION_VERIFICATION_FAILED,
    TurnResult,
    VerificationHook,
)


def make_parse_feedback(parse_errors: list[str]) -> str:
    if not parse_errors:
        return ""
    return (
        "Malformed tool-call arguments were ignored. Re-issue the intended tool call "
        "with valid JSON arguments only.\n"
        + "\n".join(f"- {err}" for err in parse_errors[:3])
    )


def clean_text_content(text_content: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text_content, flags=re.DOTALL).strip()


def build_action_dict(tool_uses: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tool_uses:
        return None
    return {"tool": tool_uses[0]["name"], "args": tool_uses[0]["input"]}


def check_retry_edit_policy(state: Any, tool_uses: list[dict[str, Any]]) -> str:
    if not getattr(state, "awaiting_retry_verification", False):
        return ""

    write_paths = [
        str(tool_use["input"].get("path", ""))
        for tool_use in tool_uses
        if tool_use["name"] == "write_file"
    ]
    if not write_paths:
        return ""

    distinct_paths = list(dict.fromkeys(path for path in write_paths if path))
    if not distinct_paths:
        return ""

    if state.retry_edit_target is None:
        state.retry_edit_target = distinct_paths[0]

    if any(path != state.retry_edit_target for path in distinct_paths):
        return (
            "The previous command already failed. Before rerunning tests, stay on one file only. "
            f"You already started fixing `{state.retry_edit_target}`. Read other files if needed, "
            "but do not edit a second file until you rerun the verification command."
        )

    return ""


def parse_model_turn(response: dict[str, Any], *, model_turn_cls: type[Any]) -> Any:
    text_content = " ".join(
        block["text"]
        for block in response.get("content", [])
        if block.get("type") == "text"
    )
    tool_uses = response.get("tool_uses", [])
    parse_errors = response.get("parse_errors", [])
    return model_turn_cls(
        text_content=text_content,
        tool_uses=tool_uses,
        parse_errors=parse_errors,
        parse_feedback=make_parse_feedback(parse_errors),
    )


async def handle_parse_only_turn(
    agent: Any,
    state: Any,
    turn: Any,
    *,
    step_start: float,
) -> None:
    agent._safe_print()
    record_trajectory_step(
        agent,
        state,
        thought=turn.text_content or "[tool call parse failure]",
        action=None,
        observation=turn.parse_feedback,
        timestamp=step_start,
        error_type="ToolCallParseError",
        is_retry=False,
    )
    state.exception_stage = "history.add_parse_feedback"
    await agent.history.add_message(
        "assistant",
        turn.text_content or "[tool call parse failure]",
    )
    await agent.history.add_message("user", turn.parse_feedback)


async def handle_retry_edit_policy_violation(
    agent: Any,
    state: Any,
    turn: Any,
    *,
    feedback: str,
    step_start: float,
) -> None:
    agent._safe_print()
    record_trajectory_step(
        agent,
        state,
        thought=turn.text_content or "[retry edit policy violation]",
        action=build_action_dict(turn.tool_uses),
        observation=feedback,
        timestamp=step_start,
        error_type="CorrectionPolicyError",
        is_retry=False,
    )
    state.exception_stage = "history.add_retry_policy_feedback"
    await agent.history.add_message("assistant", turn.text_content or "[retry edit policy violation]")
    await agent.history.add_message("user", feedback)


async def handle_completion_turn(
    agent: Any,
    state: Any,
    turn: Any,
    *,
    user_input: str,
    finalize_trajectory: bool,
    verification_hook: VerificationHook | None,
    max_verification_attempts: int,
    enforce_stop_verification: bool,
    step_start: float,
) -> TurnResult | None:
    agent._safe_print()
    clean_text = clean_text_content(turn.text_content)

    if verification_hook is not None and enforce_stop_verification:
        state.verification_attempts += 1
        verification_result = await run_verification_hook(verification_hook, state=state)
        if not verification_result.passed:
            failure_summary = verification_result.summary.strip() or "Verification failed."
            record_trajectory_step(
                agent,
                state,
                thought=clean_text,
                action=None,
                observation=f"[verification failed]\n{failure_summary}",
                timestamp=step_start,
                error_type="VerificationFailed",
                is_retry=False,
            )
            if state.verification_attempts >= max_verification_attempts:
                return finalize_turn(
                    agent,
                    state,
                    user_input=user_input,
                    finalize_trajectory=finalize_trajectory,
                    content=failure_summary,
                    success=False,
                    final_status="failed",
                    termination_reason=TERMINATION_VERIFICATION_FAILED,
                    error_details=[failure_summary],
                )

            state.exception_stage = "history.add_verification_feedback"
            await agent.history.add_message("assistant", clean_text)
            await agent.history.add_message(
                "user",
                (
                    "External verification failed. Fix the implementation and only "
                    "stop after verification passes.\n\n"
                    f"{failure_summary}"
                ),
            )
            return None

    record_trajectory_step(
        agent,
        state,
        thought=clean_text,
        action=None,
        observation="[task complete]",
        timestamp=step_start,
    )
    return finalize_turn(
        agent,
        state,
        user_input=user_input,
        finalize_trajectory=finalize_trajectory,
        content=clean_text,
        success=True,
        final_status="success",
        termination_reason=TERMINATION_MODEL_STOP,
        error_details=[],
        partial_score=1.0,
    )


def print_tool_call_preview(agent: Any, tool_uses: list[dict[str, Any]]) -> None:
    agent._safe_print()
    for tool_use in tool_uses:
        args_preview = ", ".join(
            f"{key}={repr(value)[:40]}"
            for key, value in tool_use["input"].items()
        )
        agent._safe_print(f"  > {tool_use['name']}({args_preview})")


async def add_assistant_tool_call_message(
    agent: Any,
    *,
    text_content: str,
    tool_uses: list[dict[str, Any]],
) -> None:
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
    await agent.history.add_message(
        "assistant",
        text_content,
        tool_calls=openai_tool_calls,
    )
