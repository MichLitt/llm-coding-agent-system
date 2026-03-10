import inspect
import json
import re
import time
import traceback
from typing import Any, Awaitable, Callable

from coder_agent.config import cfg
from coder_agent.core.agent_errors import classify_error, extract_exit_code, extract_stderr
from coder_agent.core.agent_types import (
    TERMINATION_LOOP_EXCEPTION,
    TERMINATION_MAX_STEPS,
    TERMINATION_MODEL_STOP,
    TERMINATION_RETRY_EXHAUSTED,
    TERMINATION_TOOL_EXCEPTION,
    TERMINATION_TOOL_NONZERO_EXIT,
    TERMINATION_VERIFICATION_FAILED,
    TERMINATION_VERIFICATION_PASSED,
    TurnResult,
    VerificationHook,
    VerificationResult,
)
from coder_agent.memory.trajectory import Step


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
    start_time = time.time()
    steps = 0
    all_tool_calls: list[str] = []
    retry_count = 0
    retry_steps = 0
    last_error_type: str | None = None
    last_error_signature: str | None = None
    project_id: str | None = None
    traj_id: str | None = None
    verification_attempts = 0
    exception_stage = "init"

    exception_stage = "history.add_user"
    await agent.history.add_message("user", user_input)

    if agent.decomposer is not None:
        exception_stage = "decomposer.decompose"
        agent._safe_print("\n[Decomposer] Generating task checklist...")
        goals = await agent.decomposer.decompose(user_input, agent.client)
        if goals:
            checklist_intro = "I've broken down this task into sub-goals:\n" + "\n".join(
                f"  [{i}] {g}" for i, g in enumerate(goals, 1)
            )
            agent._safe_print(checklist_intro)
            await agent.history.add_message("user", checklist_intro)

    if agent.memory:
        exception_stage = "memory.lookup"
        project_id = agent.memory.get_or_create_project(cfg.agent.workspace)
        recent = agent.memory.get_recent_tasks(project_id, n=3)
        if recent:
            summary_lines = ["Recent tasks in this project:"]
            for task in recent:
                status = "OK" if task["success"] else "ERR"
                summary_lines.append(f"  {status} {task['description']} ({task['steps']} steps)")
            await agent.history.add_message("user", "\n".join(summary_lines))

    if agent.trajectory_store:
        traj_id = agent.trajectory_store.start_trajectory(
            task_id=task_id or user_input[:40],
            experiment_id=agent.experiment_id,
            config=agent.experiment_config,
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
                    agent._safe_print(think_buf, end="")
                    think_buf = ""
                    return
                agent._safe_print(think_buf[:start], end="")
                in_think = True
                think_buf = think_buf[start + len("<think>") :]

    async def run_verification() -> VerificationResult:
        nonlocal exception_stage
        exception_stage = "verification_hook"
        verification_result = verification_hook()
        if inspect.isawaitable(verification_result):
            verification_result = await verification_result
        return verification_result

    try:
        for _ in range(cfg.agent.max_steps):
            steps += 1
            step_start = time.time()
            in_think = False
            think_buf = ""
            agent.history.truncate()

            if agent.decomposer is not None and steps > 1:
                agent.decomposer.update(
                    [{"observation": msg.get("content", "")} for msg in agent.history.messages[-6:]]
                )
                progress = agent.decomposer.to_progress_prompt()
                if progress:
                    await agent.history.add_message("user", progress)

            exception_stage = "llm.chat"
            response = await agent.client.chat(
                messages=agent.history.format_for_api(),
                system=agent.system,
                tools=[tool.to_dict() for tool in agent.tools],
                on_token=on_token,
                **agent._params(),
            )
            text_content = " ".join(
                block["text"]
                for block in response.get("content", [])
                if block.get("type") == "text"
            )
            tool_uses = response.get("tool_uses", [])
            parse_errors = response.get("parse_errors", [])
            parse_feedback = ""
            if parse_errors:
                parse_feedback = (
                    "Malformed tool-call arguments were ignored. Re-issue the intended tool call "
                    "with valid JSON arguments only.\n"
                    + "\n".join(f"- {err}" for err in parse_errors[:3])
                )
            all_tool_calls.extend(tool_use["name"] for tool_use in tool_uses)

            if parse_errors and not tool_uses:
                agent._safe_print()
                if traj_id:
                    agent.trajectory_store.record_step(
                        traj_id,
                        Step(
                            step_id=steps,
                            thought=text_content or "[tool call parse failure]",
                            action=None,
                            observation=parse_feedback[:500],
                            timestamp=step_start,
                            error_type="ToolCallParseError",
                            is_retry=False,
                        ),
                    )
                exception_stage = "history.add_parse_feedback"
                await agent.history.add_message(
                    "assistant",
                    text_content or "[tool call parse failure]",
                )
                await agent.history.add_message("user", parse_feedback)
                continue

            if not tool_uses:
                agent._safe_print()
                clean_text = re.sub(r"<think>.*?</think>", "", text_content, flags=re.DOTALL).strip()

                if verification_hook is not None and enforce_stop_verification:
                    verification_attempts += 1
                    verification_result = await run_verification()

                    if not verification_result.passed:
                        failure_summary = verification_result.summary.strip() or "Verification failed."
                        if traj_id:
                            agent.trajectory_store.record_step(
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
                                agent.trajectory_store.finish_trajectory(
                                    traj_id,
                                    final_status="failed",
                                    termination_reason=TERMINATION_VERIFICATION_FAILED,
                                    total_tokens=agent.history.total_tokens,
                                    duration=time.time() - start_time,
                                )
                            result = agent._make_result(
                                content=failure_summary,
                                steps=steps,
                                tool_calls=all_tool_calls,
                                success=False,
                                retry_steps=retry_steps,
                                total_tokens=agent.history.total_tokens,
                                trajectory_id=traj_id,
                                final_status="failed",
                                termination_reason=TERMINATION_VERIFICATION_FAILED,
                                error_details=[failure_summary],
                            )
                            if finalize_trajectory and agent.memory and project_id:
                                agent.memory.record_task(project_id, user_input, result)
                            return result

                        exception_stage = "history.add_verification_feedback"
                        await agent.history.add_message("assistant", clean_text)
                        await agent.history.add_message(
                            "user",
                            (
                                "External verification failed. Fix the implementation and only "
                                "stop after verification passes.\n\n"
                                f"{failure_summary}"
                            ),
                        )
                        continue

                if traj_id:
                    agent.trajectory_store.record_step(
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
                        agent.trajectory_store.finish_trajectory(
                            traj_id,
                            final_status="success",
                            termination_reason=TERMINATION_MODEL_STOP,
                            partial_score=1.0,
                            total_tokens=agent.history.total_tokens,
                            duration=time.time() - start_time,
                        )

                result = TurnResult(
                    content=clean_text,
                    steps=steps,
                    tool_calls=all_tool_calls,
                    success=True,
                    retry_steps=retry_steps,
                    total_tokens=agent.history.total_tokens,
                    trajectory_id=traj_id,
                    final_status="success",
                    termination_reason=TERMINATION_MODEL_STOP,
                    error_details=[],
                )
                if finalize_trajectory and agent.memory and project_id:
                    agent.memory.record_task(project_id, user_input, result)
                return result

            agent._safe_print()
            for tool_use in tool_uses:
                args_preview = ", ".join(
                    f"{key}={repr(value)[:40]}"
                    for key, value in tool_use["input"].items()
                )
                agent._safe_print(f"  > {tool_use['name']}({args_preview})")

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

            exception_stage = "tools.execute"
            tool_results = await execute_tools_fn(tool_uses, agent.tool_dict)
            for tool_result in tool_results:
                status = "ERR" if tool_result.get("is_error") else "ok"
                preview = str(tool_result.get("content", "")).split("\n")[0][:80]
                agent._safe_print(f"    {status}: {preview}")

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
            tool_exception = next(
                (tool_result for tool_result in tool_results if tool_result.get("is_error")),
                None,
            )
            if tool_exception is not None:
                if traj_id:
                    action_dict = (
                        {"tool": tool_uses[0]["name"], "args": tool_uses[0]["input"]}
                        if tool_uses else None
                    )
                    agent.trajectory_store.record_step(
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
                    agent.trajectory_store.finish_trajectory(
                        traj_id,
                        final_status="failed",
                        termination_reason=TERMINATION_TOOL_EXCEPTION,
                        total_tokens=agent.history.total_tokens,
                        duration=time.time() - start_time,
                    )
                result = agent._make_result(
                    content=str(tool_exception.get("content", "Error: tool execution failed")),
                    steps=steps,
                    tool_calls=all_tool_calls,
                    success=False,
                    retry_steps=retry_steps,
                    total_tokens=agent.history.total_tokens,
                    trajectory_id=traj_id,
                    final_status="failed",
                    termination_reason=TERMINATION_TOOL_EXCEPTION,
                    error_details=[str(tool_exception.get("content", "Error: tool execution failed"))],
                )
                if finalize_trajectory and agent.memory and project_id:
                    agent.memory.record_task(project_id, user_input, result)
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
            correction_enabled = agent.experiment_config.get("correction", cfg.agent.enable_correction)
            if saw_nonzero_exit:
                retry_steps += 1

            if (
                auto_complete_on_verification
                and verification_hook is not None
                and not saw_nonzero_exit
                and any(tool_use["name"] in {"write_file", "run_command"} for tool_use in tool_uses)
            ):
                verification_result = await run_verification()
                if verification_result.passed:
                    success_summary = verification_result.summary.strip() or "External verification passed."
                    verification_note = f"[external verification passed]\n{success_summary}"
                    success_observation = (
                        f"{combined_observation}\n\n{verification_note}"
                        if combined_observation else verification_note
                    )
                    if traj_id:
                        action_dict = (
                            {"tool": tool_uses[0]["name"], "args": tool_uses[0]["input"]}
                            if tool_uses else None
                        )
                        agent.trajectory_store.record_step(
                            traj_id,
                            Step(
                                step_id=steps,
                                thought=text_content,
                                action=action_dict,
                                observation=success_observation[:500],
                                timestamp=step_start,
                                error_type=None,
                                is_retry=False,
                            ),
                        )
                        if finalize_trajectory:
                            agent.trajectory_store.finish_trajectory(
                                traj_id,
                                final_status="success",
                                termination_reason=TERMINATION_VERIFICATION_PASSED,
                                partial_score=1.0,
                                total_tokens=agent.history.total_tokens,
                                duration=time.time() - start_time,
                            )
                    result = agent._make_result(
                        content=success_summary,
                        steps=steps,
                        tool_calls=all_tool_calls,
                        success=True,
                        retry_steps=retry_steps,
                        total_tokens=agent.history.total_tokens,
                        trajectory_id=traj_id,
                        final_status="success",
                        termination_reason=TERMINATION_VERIFICATION_PASSED,
                        error_details=[],
                    )
                    if finalize_trajectory and agent.memory and project_id:
                        agent.memory.record_task(project_id, user_input, result)
                    return result

            if saw_nonzero_exit and not correction_enabled:
                if traj_id:
                    action_dict = (
                        {"tool": tool_uses[0]["name"], "args": tool_uses[0]["input"]}
                        if tool_uses else None
                    )
                    agent.trajectory_store.record_step(
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
                    agent.trajectory_store.finish_trajectory(
                        traj_id,
                        final_status="failed",
                        termination_reason=TERMINATION_TOOL_NONZERO_EXIT,
                        total_tokens=agent.history.total_tokens,
                        duration=time.time() - start_time,
                    )
                result = agent._make_result(
                    content=combined_observation,
                    steps=steps,
                    tool_calls=all_tool_calls,
                    success=False,
                    retry_steps=retry_steps,
                    total_tokens=agent.history.total_tokens,
                    trajectory_id=traj_id,
                    final_status="failed",
                    termination_reason=TERMINATION_TOOL_NONZERO_EXIT,
                    error_details=stderr_parts,
                )
                if finalize_trajectory and agent.memory and project_id:
                    agent.memory.record_task(project_id, user_input, result)
                return result

            if correction_enabled and saw_nonzero_exit:
                retry_count += 1
                error_signature = f"{detected_error}:{stderr_text.strip()[:200]}"
                repeated_error = error_signature == last_error_signature
                guidance = agent._build_error_guidance(
                    detected_error,
                    stderr_text,
                    repeated=repeated_error,
                )
                if guidance and (detected_error != last_error_type or repeated_error) and retry_count <= cfg.agent.max_retries:
                    combined_observation += f"\n\n[Self-correction hint - {detected_error}]: {guidance}"
                last_error_type = detected_error
                last_error_signature = error_signature

            if retry_count > cfg.agent.max_retries:
                if traj_id and finalize_trajectory:
                    agent.trajectory_store.finish_trajectory(
                        traj_id,
                        final_status="failed",
                        termination_reason=TERMINATION_RETRY_EXHAUSTED,
                        total_tokens=agent.history.total_tokens,
                        duration=time.time() - start_time,
                    )
                result = agent._make_result(
                    content=f"Error: max retries ({cfg.agent.max_retries}) exceeded for {last_error_type}",
                    steps=steps,
                    tool_calls=all_tool_calls,
                    success=False,
                    retry_steps=retry_steps,
                    total_tokens=agent.history.total_tokens,
                    trajectory_id=traj_id,
                    final_status="failed",
                    termination_reason=TERMINATION_RETRY_EXHAUSTED,
                    error_details=[f"Error: max retries ({cfg.agent.max_retries}) exceeded for {last_error_type}"],
                )
                if finalize_trajectory and agent.memory and project_id:
                    agent.memory.record_task(project_id, user_input, result)
                return result

            if traj_id:
                action_dict = None
                if tool_uses:
                    action_dict = {"tool": tool_uses[0]["name"], "args": tool_uses[0]["input"]}
                agent.trajectory_store.record_step(
                    traj_id,
                    Step(
                        step_id=steps,
                        thought=text_content,
                        action=action_dict,
                        observation=combined_observation[:500],
                        timestamp=step_start,
                        error_type=detected_error if saw_nonzero_exit else ("ToolCallParseError" if parse_errors else None),
                        is_retry=saw_nonzero_exit,
                    ),
                )

            for tool_result in tool_results:
                exception_stage = "history.add_tool"
                await agent.history.add_message(
                    "tool",
                    tool_result.get("content", ""),
                    tool_calls=[{"id": tool_result["tool_use_id"]}],
                )
            if parse_feedback:
                exception_stage = "history.add_parse_feedback"
                await agent.history.add_message("user", parse_feedback)

    except Exception as exc:
        tb_summary = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        error_summary = (
            f"Exception stage: {exception_stage}\n"
            f"Exception class: {type(exc).__name__}\n"
            f"Message: {exc}\n"
            f"Traceback:\n{tb_summary[:1200]}"
        )
        if traj_id:
            agent.trajectory_store.record_step(
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
            agent.trajectory_store.finish_trajectory(
                traj_id,
                final_status="failed",
                termination_reason=TERMINATION_LOOP_EXCEPTION,
                total_tokens=agent.history.total_tokens,
                duration=time.time() - start_time,
            )
        result = agent._make_result(
            content=error_summary,
            steps=steps,
            tool_calls=all_tool_calls,
            success=False,
            retry_steps=retry_steps,
            total_tokens=agent.history.total_tokens,
            trajectory_id=traj_id,
            final_status="failed",
            termination_reason=TERMINATION_LOOP_EXCEPTION,
            error_details=[error_summary],
        )
        if finalize_trajectory and agent.memory and project_id:
            agent.memory.record_task(project_id, user_input, result)
        return result

    if traj_id and finalize_trajectory:
        agent.trajectory_store.finish_trajectory(
            traj_id,
            final_status="timeout",
            termination_reason=TERMINATION_MAX_STEPS,
            total_tokens=agent.history.total_tokens,
            duration=time.time() - start_time,
        )
    result = agent._make_result(
        content="Error: max steps reached",
        steps=steps,
        tool_calls=all_tool_calls,
        success=False,
        retry_steps=retry_steps,
        total_tokens=agent.history.total_tokens,
        trajectory_id=traj_id,
        final_status="timeout",
        termination_reason=TERMINATION_MAX_STEPS,
        error_details=["Error: max steps reached"],
    )
    if finalize_trajectory and agent.memory and project_id:
        agent.memory.record_task(project_id, user_input, result)
    return result
