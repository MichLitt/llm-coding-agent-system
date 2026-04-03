"""Benchmark-first evaluation metrics for the Coder-Agent."""

from dataclasses import dataclass, field


@dataclass
class EvalResult:
    """Result for a single task evaluation."""

    task_id: str
    success: bool  # legacy strict-success field
    benchmark_passed: bool
    agent_completed_cleanly: bool
    agent_final_status: str
    checks_passed: int
    checks_total: int
    steps_used: int
    retry_steps: int
    termination_reason: str | None = None
    verification_pass_rate: float = 0.0
    total_tokens: int = 0
    duration: float = 0.0
    error_types: list[str] = field(default_factory=list)
    activation_counters: dict = field(default_factory=dict)
    config_label: str = ""


@dataclass
class MetricsSummary:
    """Aggregated metrics over a suite of EvalResult objects."""

    config_label: str
    n_tasks: int
    task_success_rate: float
    benchmark_pass_rate: float
    clean_completion_rate: float
    strict_success_rate: float
    partial_credit_score: float
    efficiency_score: float
    retry_cost: float
    avg_steps: float
    avg_tokens: float
    avg_duration: float
    by_difficulty: dict[str, float] = field(default_factory=dict)


def benchmark_pass_rate(results: list[EvalResult]) -> float:
    """Fraction of tasks where benchmark verification passed."""
    if not results:
        return 0.0
    return sum(1 for result in results if result.benchmark_passed) / len(results)


def task_success_rate(results: list[EvalResult]) -> float:
    """Legacy alias retained for backward compatibility."""
    return benchmark_pass_rate(results)


def clean_completion_rate(results: list[EvalResult]) -> float:
    """Fraction of tasks where the agent ended with final_status='success'."""
    if not results:
        return 0.0
    return sum(1 for result in results if result.agent_completed_cleanly) / len(results)


def strict_success_rate(results: list[EvalResult]) -> float:
    """Fraction of tasks that passed benchmark checks and completed cleanly."""
    if not results:
        return 0.0
    return sum(1 for result in results if result.success) / len(results)


def partial_credit_score(results: list[EvalResult]) -> float:
    """Average fraction of checks passed across all tasks."""
    if not results:
        return 0.0

    scores = []
    for result in results:
        if result.checks_total > 0:
            if result.verification_pass_rate > 0:
                scores.append(result.verification_pass_rate)
            else:
                scores.append(result.checks_passed / result.checks_total)
        else:
            scores.append(1.0 if result.benchmark_passed else 0.0)
    return sum(scores) / len(scores)


def efficiency_score(results: list[EvalResult]) -> float:
    """Mean efficiency for benchmark-passed tasks: 1 / steps_used."""
    passed = [
        result
        for result in results
        if result.benchmark_passed and result.steps_used > 0
    ]
    if not passed:
        return 0.0
    return sum(1.0 / result.steps_used for result in passed) / len(passed)


def retry_cost(results: list[EvalResult]) -> float:
    """Mean fraction of steps spent on correction retries."""
    valid = [result for result in results if result.steps_used > 0]
    if not valid:
        return 0.0
    return sum(result.retry_steps / result.steps_used for result in valid) / len(valid)


def compute_metrics(results: list[EvalResult], config_label: str = "") -> MetricsSummary:
    """Compute aggregate benchmark-first metrics."""
    if not results:
        return MetricsSummary(
            config_label=config_label,
            n_tasks=0,
            task_success_rate=0.0,
            benchmark_pass_rate=0.0,
            clean_completion_rate=0.0,
            strict_success_rate=0.0,
            partial_credit_score=0.0,
            efficiency_score=0.0,
            retry_cost=0.0,
            avg_steps=0.0,
            avg_tokens=0.0,
            avg_duration=0.0,
        )

    label = config_label or results[0].config_label
    return MetricsSummary(
        config_label=label,
        n_tasks=len(results),
        task_success_rate=benchmark_pass_rate(results),
        benchmark_pass_rate=benchmark_pass_rate(results),
        clean_completion_rate=clean_completion_rate(results),
        strict_success_rate=strict_success_rate(results),
        partial_credit_score=partial_credit_score(results),
        efficiency_score=efficiency_score(results),
        retry_cost=retry_cost(results),
        avg_steps=sum(result.steps_used for result in results) / len(results),
        avg_tokens=sum(result.total_tokens for result in results) / len(results),
        avg_duration=sum(result.duration for result in results) / len(results),
    )


def print_metrics_table(summaries: list[MetricsSummary]) -> None:
    """Print a comparison table of metrics across configurations."""
    header = (
        f"{'Config':<18} {'N':<5} {'Bench':>8} {'Clean':>8} {'Strict':>8} "
        f"{'Partial':>8} {'Efficiency':>11} {'RetryCost':>10} "
        f"{'AvgSteps':>9} {'AvgTokens':>10}"
    )
    print(header)
    print("-" * len(header))
    for summary in summaries:
        print(
            f"{summary.config_label:<18} {summary.n_tasks:<5} "
            f"{summary.benchmark_pass_rate:>8.1%} "
            f"{summary.clean_completion_rate:>8.1%} "
            f"{summary.strict_success_rate:>8.1%} "
            f"{summary.partial_credit_score:>8.1%} "
            f"{summary.efficiency_score:>11.4f} "
            f"{summary.retry_cost:>10.1%} "
            f"{summary.avg_steps:>9.1f} "
            f"{summary.avg_tokens:>10.0f}"
        )
