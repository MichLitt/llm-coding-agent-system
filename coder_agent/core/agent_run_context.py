import inspect
import time
from typing import Any

from coder_agent.config import cfg
from coder_agent.core.agent_types import TurnResult, VerificationHook, VerificationResult
from coder_agent.memory.trajectory import Step

_OBSERVATION_MAX_CHARS = 500


def record_trajectory_step(
    agent: Any,
    state: Any,
    *,
    thought: str,
    action: dict[str, Any] | None,
    observation: str,
    timestamp: float,
    error_type: str | None = None,
    is_retry: bool = False,
) -> None:
    if not state.traj_id:
        return
    agent.trajectory_store.record_step(
        state.traj_id,
        Step(
            step_id=state.steps,
            thought=thought,
            action=action,
            observation=(
                observation[:_OBSERVATION_MAX_CHARS] + " ...[truncated]"
                if len(observation) > _OBSERVATION_MAX_CHARS
                else observation
            ),
            timestamp=timestamp,
            error_type=error_type,
            is_retry=is_retry,
        ),
    )


def finish_trajectory(
    agent: Any,
    state: Any,
    *,
    finalize_trajectory: bool,
    final_status: str,
    termination_reason: str,
    partial_score: float = 0.0,
) -> None:
    if not state.traj_id or not finalize_trajectory:
        return
    agent.trajectory_store.finish_trajectory(
        state.traj_id,
        final_status=final_status,
        termination_reason=termination_reason,
        partial_score=partial_score,
        total_tokens=agent.history.total_tokens,
        duration=time.time() - state.start_time,
    )


def finalize_turn(
    agent: Any,
    state: Any,
    *,
    user_input: str,
    finalize_trajectory: bool,
    content: str,
    success: bool,
    final_status: str,
    termination_reason: str,
    error_details: list[str] | None = None,
    partial_score: float = 0.0,
) -> TurnResult:
    finish_trajectory(
        agent,
        state,
        finalize_trajectory=finalize_trajectory,
        final_status=final_status,
        termination_reason=termination_reason,
        partial_score=partial_score,
    )
    result = agent._make_result(
        content=content,
        steps=state.steps,
        tool_calls=state.all_tool_calls,
        success=success,
        retry_steps=state.retry_steps,
        total_tokens=agent.history.total_tokens,
        trajectory_id=state.traj_id,
        final_status=final_status,
        termination_reason=termination_reason,
        error_details=error_details or [],
    )
    return result


async def run_verification_hook(
    verification_hook: VerificationHook,
    *,
    state: Any,
) -> VerificationResult:
    state.exception_stage = "verification_hook"
    verification_result = verification_hook()
    if inspect.isawaitable(verification_result):
        verification_result = await verification_result
    return verification_result


async def seed_run_context(agent: Any, state: Any, user_input: str) -> None:
    state.exception_stage = "history.add_user"
    await agent.history.add_message("user", user_input)

    if agent.decomposer is not None:
        state.exception_stage = "decomposer.decompose"
        agent._safe_print("\n[Decomposer] Generating task checklist...")
        goals = await agent.decomposer.decompose(user_input, agent.client)
        if goals:
            checklist_intro = "I've broken down this task into sub-goals:\n" + "\n".join(
                f"  [{i}] {goal}" for i, goal in enumerate(goals, 1)
            )
            agent._safe_print(checklist_intro)
            await agent.history.add_message("user", checklist_intro)

    if agent.memory:
        state.exception_stage = "memory.lookup"
        state.project_id = agent.memory.get_or_create_project(cfg.agent.workspace)
        recent = agent.memory.get_recent_tasks(state.project_id, n=3)
        if recent:
            summary_lines = ["Recent tasks in this project:"]
            for task in recent:
                status = "OK" if task["success"] else "ERR"
                summary_lines.append(f"  {status} {task['description']} ({task['steps']} steps)")
            await agent.history.add_message("user", "\n".join(summary_lines))


def start_trajectory(agent: Any, state: Any, *, user_input: str, task_id: str) -> None:
    if not agent.trajectory_store:
        return
    state.traj_id = agent.trajectory_store.start_trajectory(
        task_id=task_id or user_input[:40],
        experiment_id=agent.experiment_id,
        config=agent.experiment_config,
    )


async def add_decomposer_progress(agent: Any, state: Any) -> None:
    if agent.decomposer is None or state.steps <= 1:
        return
    agent.decomposer.update(
        [{"observation": msg.get("content", "")} for msg in agent.history.messages[-6:]]
    )
    progress = agent.decomposer.to_progress_prompt()
    if progress:
        await agent.history.add_message("user", progress)
