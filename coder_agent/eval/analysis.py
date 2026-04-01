"""Trajectory analysis public facade."""

from pathlib import Path

from coder_agent.config import cfg
from coder_agent.eval.analysis_llm import LLMTaxonomyResult, failure_taxonomy_llm
from coder_agent.eval.analysis_stats import TrajectoryStats, compute_statistics
from coder_agent.eval.analysis_taxonomy import TaxonomyResult, failure_taxonomy, is_context_lost
from coder_agent.memory.trajectory import TrajectoryStore


class TrajectoryAnalyzer:
    """Loads trajectories and computes statistics and failure taxonomy."""

    def __init__(self, trajectory_dir: Path | None = None):
        self.store = TrajectoryStore(trajectory_dir or cfg.eval.trajectory_dir)
        self._cache: dict[str, list[dict]] = {}

    def _load(self, experiment_id: str) -> list[dict]:
        if experiment_id in self._cache:
            return self._cache[experiment_id]
        trajs = self.store.load(experiment_id)
        latest_by_key: dict[str, dict] = {}
        ordered_keys: list[str] = []
        for index, traj in enumerate(trajs):
            task_id = str(traj.get("task_id") or f"__missing__{index}")
            if task_id not in latest_by_key:
                ordered_keys.append(task_id)
            latest_by_key[task_id] = traj
        result = [latest_by_key[key] for key in ordered_keys]
        self._cache[experiment_id] = result
        return result

    def compute_statistics(self, experiment_id: str) -> TrajectoryStats:
        return compute_statistics(experiment_id, self._load(experiment_id))

    def failure_taxonomy(self, experiment_id: str) -> list[TaxonomyResult]:
        return failure_taxonomy(self._load(experiment_id))

    def failure_taxonomy_llm(self, experiment_id: str) -> list[LLMTaxonomyResult]:
        return failure_taxonomy_llm(self._load(experiment_id))

    def print_llm_taxonomy(self, results: list[LLMTaxonomyResult]) -> None:
        if not results:
            print("No failed trajectories to classify.")
            return

        print(f"\n{'=' * 60}")
        print("LLM-as-Critic Failure Taxonomy")
        print(f"{'=' * 60}")
        print(f"Classified {len(results)} failed trajectories\n")

        from collections import Counter

        align_counts = Counter(r.goal_alignment for r in results)
        print("Goal Alignment:")
        for value, count in align_counts.most_common():
            bar = "#" * int(count / len(results) * 20)
            print(f"  {value:<12} {count:>3} ({count / len(results):.1%}) {bar}")

        print()
        issue_counts = Counter(r.execution_issue for r in results)
        print("Execution Issue:")
        for value, count in issue_counts.most_common():
            bar = "#" * int(count / len(results) * 20)
            print(f"  {value:<12} {count:>3} ({count / len(results):.1%}) {bar}")

        fixable = sum(1 for r in results if r.fixable_by_more_steps)
        print()
        print(f"Fixable by more steps: {fixable}/{len(results)} ({fixable / len(results):.1%})")
        print()
        print("Per-task breakdown:")
        for result in results:
            fixable_tag = "[fixable]" if result.fixable_by_more_steps else ""
            print(
                f"  {result.task_id:<30} align={result.goal_alignment:<8} "
                f"issue={result.execution_issue:<10} {fixable_tag}"
            )
            print(f"    -> {result.explanation}")

    def print_report(self, stats: TrajectoryStats, taxonomy: list[TaxonomyResult]) -> None:
        print(f"\n{'=' * 60}")
        print(f"Trajectory Analysis: {stats.experiment_id}")
        print(f"{'=' * 60}")
        print(f"Total tasks:   {stats.total_trajectories}")
        print(f"  Success:     {stats.success_count} ({stats.success_count / max(stats.total_trajectories, 1):.1%})")
        print(f"  Failed:      {stats.failed_count}")
        print(f"  Timeout:     {stats.timeout_count}")
        print()
        print(f"Avg steps (all):     {stats.avg_steps_all:.1f}")
        print(f"Avg steps (success): {stats.avg_steps_success:.1f}")
        print(f"Avg steps (failed):  {stats.avg_steps_failed:.1f}")
        print(f"Avg tokens:          {stats.avg_tokens:.0f}")
        print(f"Avg duration:        {stats.avg_duration:.1f}s")
        print(f"Retry rate:          {stats.retry_rate:.1%}")
        print(f"Correction success:  {stats.correction_success_rate:.1%}")
        print()
        if stats.termination_reasons:
            print("Termination reasons:")
            for reason, count in sorted(stats.termination_reasons.items(), key=lambda x: (-x[1], x[0])):
                print(f"  {reason:<20} {count:>5}")
            print()
        print("Tool usage distribution:")
        for tool, count in sorted(stats.tool_usage.items(), key=lambda x: -x[1]):
            print(f"  {tool:<20} {count:>5} calls")

        if taxonomy:
            print()
            print("Failure Taxonomy:")
            for item in taxonomy:
                bar = "#" * int(item.fraction * 20)
                print(f"  {item.category:<20} {item.count:>3} ({item.fraction:.1%}) {bar}")
                if item.example_task_ids:
                    print(f"    examples: {', '.join(item.example_task_ids)}")

    def compare_experiments(self, experiment_ids: list[str]) -> None:
        print(f"\n{'=' * 70}")
        print("Experiment Comparison")
        print(f"{'=' * 70}")
        header = f"{'Metric':<30}" + "".join(f"{experiment_id:>10}" for experiment_id in experiment_ids)
        print(header)
        print("-" * len(header))

        all_stats = {experiment_id: self.compute_statistics(experiment_id) for experiment_id in experiment_ids}
        metrics = [
            ("Success Rate", lambda s: f"{s.success_count / max(s.total_trajectories, 1):.1%}"),
            ("Avg Steps", lambda s: f"{s.avg_steps_all:.1f}"),
            ("Retry Rate", lambda s: f"{s.retry_rate:.1%}"),
            ("Correction%", lambda s: f"{s.correction_success_rate:.1%}"),
            ("Avg Tokens", lambda s: f"{s.avg_tokens:.0f}"),
        ]

        for name, formatter in metrics:
            row = f"{name:<30}"
            for experiment_id in experiment_ids:
                row += f"{formatter(all_stats[experiment_id]):>10}"
            print(row)


_is_context_lost = is_context_lost

__all__ = [
    "LLMTaxonomyResult",
    "TaxonomyResult",
    "TrajectoryAnalyzer",
    "TrajectoryStats",
    "_is_context_lost",
]
