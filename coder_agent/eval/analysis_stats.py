from collections import Counter
from dataclasses import dataclass, field


@dataclass
class TrajectoryStats:
    experiment_id: str
    total_trajectories: int
    success_count: int
    failed_count: int
    timeout_count: int
    avg_steps_all: float
    avg_steps_success: float
    avg_steps_failed: float
    avg_tokens: float
    avg_duration: float
    tool_usage: dict[str, int]
    retry_rate: float
    correction_success_rate: float
    termination_reasons: dict[str, int] = field(default_factory=dict)
    by_difficulty: dict[str, dict] = field(default_factory=dict)


def correction_success_rate(trajs: list[dict]) -> float:
    retry_total = 0
    retry_resolved = 0
    for traj in trajs:
        steps = traj.get("steps", [])
        for i, step in enumerate(steps[:-1]):
            if step.get("is_retry"):
                retry_total += 1
                next_step = steps[i + 1]
                if not next_step.get("error_type"):
                    retry_resolved += 1
    return retry_resolved / retry_total if retry_total else 0.0


def compute_statistics(experiment_id: str, trajs: list[dict]) -> TrajectoryStats:
    if not trajs:
        return TrajectoryStats(
            experiment_id=experiment_id,
            total_trajectories=0,
            success_count=0,
            failed_count=0,
            timeout_count=0,
            avg_steps_all=0,
            avg_steps_success=0,
            avg_steps_failed=0,
            avg_tokens=0,
            avg_duration=0,
            tool_usage={},
            retry_rate=0,
            correction_success_rate=0,
            termination_reasons={},
        )

    success = [t for t in trajs if t["final_status"] == "success"]
    failed = [t for t in trajs if t["final_status"] == "failed"]
    timeout = [t for t in trajs if t["final_status"] == "timeout"]

    def avg_steps(items: list[dict]) -> float:
        if not items:
            return 0.0
        return sum(len(t["steps"]) for t in items) / len(items)

    tool_counter: Counter = Counter()
    total_steps = 0
    retry_steps = 0
    for traj in trajs:
        for step in traj.get("steps", []):
            total_steps += 1
            if step.get("action") and step["action"].get("tool"):
                tool_counter[step["action"]["tool"]] += 1
            if step.get("is_retry"):
                retry_steps += 1

    termination_counts = Counter(
        t.get("termination_reason")
        for t in trajs
        if t.get("termination_reason")
    )

    return TrajectoryStats(
        experiment_id=experiment_id,
        total_trajectories=len(trajs),
        success_count=len(success),
        failed_count=len(failed),
        timeout_count=len(timeout),
        avg_steps_all=avg_steps(trajs),
        avg_steps_success=avg_steps(success),
        avg_steps_failed=avg_steps(failed + timeout),
        avg_tokens=sum(t.get("total_tokens", 0) for t in trajs) / len(trajs),
        avg_duration=sum(t.get("duration", 0) for t in trajs) / len(trajs),
        tool_usage=dict(tool_counter.most_common()),
        retry_rate=retry_steps / total_steps if total_steps else 0.0,
        correction_success_rate=correction_success_rate(trajs),
        termination_reasons=dict(termination_counts),
    )
