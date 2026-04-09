import inspect
import time
from typing import Any

from coder_agent.config import cfg
from coder_agent.core.agent_types import TurnResult, VerificationHook, VerificationResult
from coder_agent.memory.trajectory import Step

_OBSERVATION_MAX_CHARS = 500


def _runtime_setting(agent: Any, key: str, default: Any) -> Any:
    runtime_config = getattr(agent, "_experiment_config", {})
    if key in runtime_config:
        return runtime_config[key]
    preset_config = getattr(agent, "experiment_config", {})
    if key in preset_config:
        return preset_config[key]
    return default


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

from coder_agent.config import cfg


async def seed_run_context(agent: Any, state: Any, user_input: str) -> None:
    state.exception_stage = "history.add_user"
    await agent.history.add_message("user", user_input)
    if not hasattr(state, "cross_task_memory_injected"):
        state.cross_task_memory_injected = False
    if not hasattr(state, "memory_injections"):
        state.memory_injections = 0
    if not hasattr(state, "db_records_written"):
        state.db_records_written = 0

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
        state.project_id = agent.memory.get_or_create_project(agent.workspace)
        lookup_mode = _runtime_setting(agent, "memory_lookup_mode", cfg.agent.memory_lookup_mode)
        if lookup_mode == "similarity":
            similar = agent.memory.get_similar_tasks(state.project_id, user_input, n=3)
            if similar:
                summary_lines = ["[Memory] Similar completed tasks in this run:"]
                for index, task in enumerate(similar, 1):
                    status = "OK" if task["success"] else "ERR"
                    termination = task.get("termination_reason") or "unknown"
                    summary_lines.append(
                        f'  {index}. [{status}] "{task["description"]}" ({task["steps"]} steps) - termination: {termination}'
                    )
                    if not task["success"] and task.get("error_summary"):
                        summary = " ".join(task["error_summary"].split())[:100]
                        summary_lines.append(f"     Summary: {summary}")
                memory_prompt = "\n".join(summary_lines)
                if len(memory_prompt) > 400:
                    memory_prompt = memory_prompt[:397].rstrip() + "..."
                await agent.history.add_message("user", memory_prompt)
                state.cross_task_memory_injected = True
                state.memory_injections += 1
        else:
            recent = agent.memory.get_recent_tasks(state.project_id, n=3)
            if recent:
                summary_lines = ["Recent tasks in this project:"]
                for task in recent:
                    status = "OK" if task["success"] else "ERR"
                    summary_lines.append(f"  {status} {task['description']} ({task['steps']} steps)")
                await agent.history.add_message("user", "\n".join(summary_lines))
                state.cross_task_memory_injected = True
                state.memory_injections += 1


def start_trajectory(agent: Any, state: Any, *, user_input: str, task_id: str) -> None:
    if not agent.trajectory_store:
        return
    start_kwargs = {
        "task_id": task_id or user_input[:40],
        "experiment_id": agent.experiment_id,
        "config": agent.experiment_config,
    }
    if "task_metadata" in inspect.signature(agent.trajectory_store.start_trajectory).parameters:
        start_kwargs["task_metadata"] = dict(getattr(state, "task_metadata", {}) or {})
    state.traj_id = agent.trajectory_store.start_trajectory(**start_kwargs)


async def add_decomposer_progress(agent: Any, state: Any) -> None:
    if agent.decomposer is None or state.steps <= 1:
        return
    agent.decomposer.update(
        [{"observation": msg.get("content", "")} for msg in agent.history.messages[-6:]]
    )
    progress = agent.decomposer.to_progress_prompt()
    if progress:
        await agent.history.add_message("user", progress)
