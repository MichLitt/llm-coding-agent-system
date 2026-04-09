import json
import re
from typing import Any

from coder_agent.core.agent_errors import extract_first_test_target
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


def _edit_paths(tool_uses: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for tool_use in tool_uses:
        if tool_use["name"] not in {"write_file", "patch_file"}:
            continue
        path = str(tool_use["input"].get("path", "")).strip()
        if path:
            paths.append(path)
    return paths


def _distinct_paths(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(path for path in paths if path))


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _authorized_test_paths(state: Any) -> set[str]:
    metadata = getattr(state, "task_metadata", {}) or {}
    authorized = {
        str(path).strip()
        for path in metadata.get("authorized_test_edit_paths", [])
        if str(path).strip()
    }
    authorized.update(
        str(path).strip()
        for path in metadata.get("verification_files", [])
        if str(path).strip()
    )
    return authorized


def check_retry_edit_policy(agent: Any, state: Any, tool_uses: list[dict[str, Any]]) -> str:
    write_paths = _edit_paths(tool_uses)
    if not write_paths:
        return ""

    distinct_paths = _distinct_paths(write_paths)
    if not distinct_paths:
        return ""

    recovery_mode = getattr(state, "recovery_mode", "none")
    if recovery_mode == "tool_error":
        if state.retry_edit_target is None:
            state.retry_edit_target = distinct_paths[0]
        if any(path != state.retry_edit_target for path in distinct_paths):
            return (
                "The previous command already failed. Before rerunning tests, stay on one file only. "
                f"You already started fixing `{state.retry_edit_target}`. Read other files if needed, "
                "but do not edit a second file until you rerun the verification command."
            )

    if recovery_mode != "verification":
        return ""

    metadata = getattr(state, "task_metadata", {}) or {}
    if not metadata:
        return ""

    runtime_config = getattr(agent, "_experiment_config", {})
    impl_paths = [path for path in distinct_paths if not _is_test_path(path)]
    test_paths = [path for path in distinct_paths if _is_test_path(path)]
    authorized_test_paths = _authorized_test_paths(state)
    allow_unlisted = bool(
        runtime_config.get(
            "allow_unlisted_test_edits_during_verification_recovery",
            False,
        )
    )

    last_target = str(getattr(state, "last_verification_failure_target", "") or "")
    failing_target_file = last_target.split("::", 1)[0] if last_target else ""
    if test_paths and not allow_unlisted:
        allowed_test_paths = set(authorized_test_paths)
        if failing_target_file and _is_test_path(failing_target_file):
            allowed_test_paths.add(failing_target_file)
        unauthorized = [path for path in test_paths if path not in allowed_test_paths]
        if unauthorized:
            return (
                "Verification recovery blocked an unlisted test edit. "
                f"Authorized test paths: {', '.join(sorted(allowed_test_paths)) or '(none)'}. "
                f"Attempted test edit(s): {', '.join(unauthorized)}. "
                "Start with implementation files or benchmark-authorized regression tests only."
            )

    prefer_expected_targets = bool(
        runtime_config.get("prefer_expected_patch_targets", True)
    )
    expected_targets = [str(path).strip() for path in metadata.get("expected_patch_targets", []) if str(path).strip()]
    expected_impl_targets = [path for path in expected_targets if not _is_test_path(path)]
    if (
        test_paths
        and expected_impl_targets
        and not getattr(state, "verification_recovery_impl_paths", set())
        and not authorized_test_paths
        and prefer_expected_targets
    ):
        return (
            "Verification recovery should start from implementation files before touching tests. "
            f"Prefer expected implementation target(s): {', '.join(expected_impl_targets[:2])}."
        )

    max_impl_edits = int(
        runtime_config.get("max_impl_edit_files_per_verification_recovery", 2)
    )
    next_impl_paths = set(getattr(state, "verification_recovery_impl_paths", set()))
    next_impl_paths.update(impl_paths)
    if max_impl_edits >= 0 and len(next_impl_paths) > max_impl_edits:
        return (
            "Verification recovery edit cap reached. "
            f"You may edit at most {max_impl_edits} implementation file(s) before rerunning verification. "
            f"Already touched: {', '.join(sorted(getattr(state, 'verification_recovery_impl_paths', set())))}."
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
        verification_result = await run_verification_hook(verification_hook, state=state)
        if not verification_result.passed:
            failure_summary = verification_result.summary.strip() or "Verification failed."
            failure_signature = failure_summary.splitlines()[0].strip()[:200]
            failing_target = extract_first_test_target(failure_summary)
            repeated_failure = failure_signature == getattr(
                state, "last_verification_failure_signature", None
            )
            state.last_verification_failure_signature = failure_signature
            state.last_verification_failure_target = failing_target
            state.verification_failures += 1
            state.consecutive_verification_failures = (
                state.consecutive_verification_failures + 1 if repeated_failure else 1
            )
            counted_attempt = bool(getattr(state, "verification_recovery_action_seen", False))
            if counted_attempt:
                state.verification_attempts += 1
            else:
                state.no_tool_completion_verification_failures += 1
            diagnostic_observation = (
                "[verification failed]"
                f"[completion-without-tools]"
                f"[attempt {state.verification_attempts}/{max_verification_attempts}]"
                f"[counted={'yes' if counted_attempt else 'no'}]\n"
                f"{failure_summary}"
            )
            record_trajectory_step(
                agent,
                state,
                thought=clean_text,
                action=None,
                observation=diagnostic_observation,
                timestamp=step_start,
                error_type="VerificationFailed",
                is_retry=False,
            )
            if counted_attempt and state.verification_attempts >= max_verification_attempts:
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

            guidance = agent._build_verification_guidance(
                failure_summary,
                repeated=repeated_failure or state.no_tool_completion_verification_failures > 1,
                counted_attempt=counted_attempt,
                preferred_patch_targets=list((getattr(state, "task_metadata", {}) or {}).get("expected_patch_targets", [])),
                stronger_feedback=state.no_tool_completion_verification_failures > 1,
            )
            state.verification_recovery_action_seen = False
            state.recovery_mode = "verification"
            state.retry_edit_target = None
            state.verification_recovery_impl_paths = set()
            state.exception_stage = "history.add_verification_feedback"
            await agent.history.add_message("assistant", clean_text or "[completion without tools]")
            await agent.history.add_message("user", guidance)
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
