"""Trajectory analysis: Failure Taxonomy + statistical summaries.

Two modes:
1. Rule-based taxonomy (fast, no LLM) — classifies failures by error_type in steps.
2. LLM-as-Critic taxonomy (--llm-taxonomy) — two-dimensional classification via LLM.

Usage
-----
    from coder_agent.eval.analysis import TrajectoryAnalyzer

    analyzer = TrajectoryAnalyzer(trajectory_dir=cfg.eval.trajectory_dir)
    stats = analyzer.compute_statistics(experiment_id="C3")
    taxonomy = analyzer.failure_taxonomy(experiment_id="C3")
    analyzer.print_report(stats, taxonomy)

    # LLM-based deep analysis
    llm_results = analyzer.failure_taxonomy_llm(experiment_id="C3")
    analyzer.print_llm_taxonomy(llm_results)
"""

import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coder_agent.config import cfg
from coder_agent.memory.trajectory import TrajectoryStore


# ---------------------------------------------------------------------------
# Failure taxonomy categories
# ---------------------------------------------------------------------------

_TAXONOMY_RULES = [
    ("Planning Error",  lambda t: _is_planning_error(t)),
    ("Tool Error",      lambda t: _has_error_type(t, "tool_not_found") or _has_tool_error(t)),
    ("Syntax Error",    lambda t: _has_error_type(t, "SyntaxError")),
    ("Import Error",    lambda t: _has_error_type(t, "ImportError")),
    ("Logic Error",     lambda t: _has_error_type(t, "LogicError") or _has_error_type(t, "AssertionError")),
    ("Context Lost",    lambda t: _is_context_lost(t)),
    ("Timeout",         lambda t: t.get("final_status") == "timeout"),
    ("Other",           lambda t: True),  # fallback
]


def _has_error_type(traj: dict, error_type: str) -> bool:
    return any(s.get("error_type") == error_type for s in traj.get("steps", []))


def _has_tool_error(traj: dict) -> bool:
    return any(
        "tool" in str(s.get("observation", "")).lower() and "not found" in str(s.get("observation", "")).lower()
        for s in traj.get("steps", [])
    )


def _is_planning_error(traj: dict) -> bool:
    steps = traj.get("steps", [])
    if len(steps) < 3:
        return False
    # Signs: repeating same tool with same args, or many steps with no progress
    tool_action_pairs = [
        (s.get("action", {}).get("tool", ""), str(s.get("action", {}).get("args", {}))[:100])
        for s in steps if s.get("action")
    ]
    # Check for repeated identical actions (stuck in loop)
    if len(tool_action_pairs) > 2:
        for i in range(len(tool_action_pairs) - 2):
            if tool_action_pairs[i] == tool_action_pairs[i + 1] == tool_action_pairs[i + 2]:
                return True
    return False


def _is_context_lost(traj: dict) -> bool:
    steps = traj.get("steps", [])
    # Sign: agent revisits already-completed actions (re-writes same file)
    write_actions = [
        (s.get("action") or {}).get("args", {}).get("path", "")
        for s in steps
        if (s.get("action") or {}).get("tool") in ("write_file",)
    ]
    seen = set()
    for path in write_actions:
        if path in seen:
            return True
        seen.add(path)
    return False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TaxonomyResult:
    category: str
    count: int
    fraction: float
    example_task_ids: list[str] = field(default_factory=list)


@dataclass
class LLMTaxonomyResult:
    """Result of LLM-as-Critic two-dimensional failure classification."""
    task_id: str
    goal_alignment: str       # "correct" | "deviated"
    execution_issue: str      # "logic" | "tool" | "self_eval" | "planning" | "none"
    explanation: str
    fixable_by_more_steps: bool
    final_status: str
    steps_used: int


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

    tool_usage: dict[str, int]          # tool name -> total calls
    retry_rate: float                    # fraction of steps that are retries
    correction_success_rate: float       # when retry happened, did the next step succeed?
    termination_reasons: dict[str, int] = field(default_factory=dict)

    by_difficulty: dict[str, dict] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class TrajectoryAnalyzer:
    """Loads trajectories and computes statistics and failure taxonomy."""

    def __init__(self, trajectory_dir: Path | None = None):
        self.store = TrajectoryStore(trajectory_dir or cfg.eval.trajectory_dir)

    def _load(self, experiment_id: str) -> list[dict]:
        trajs = self.store.load(experiment_id)
        latest_by_key: dict[str, dict] = {}
        ordered_keys: list[str] = []
        for index, traj in enumerate(trajs):
            task_id = str(traj.get("task_id") or f"__missing__{index}")
            if task_id not in latest_by_key:
                ordered_keys.append(task_id)
            latest_by_key[task_id] = traj
        return [latest_by_key[key] for key in ordered_keys]

    def compute_statistics(self, experiment_id: str) -> TrajectoryStats:
        """Compute aggregate statistics for an experiment."""
        trajs = self._load(experiment_id)
        if not trajs:
            return TrajectoryStats(
                experiment_id=experiment_id,
                total_trajectories=0,
                success_count=0, failed_count=0, timeout_count=0,
                avg_steps_all=0, avg_steps_success=0, avg_steps_failed=0,
                avg_tokens=0, avg_duration=0,
                tool_usage={}, retry_rate=0, correction_success_rate=0,
                termination_reasons={},
            )

        success = [t for t in trajs if t["final_status"] == "success"]
        failed  = [t for t in trajs if t["final_status"] == "failed"]
        timeout = [t for t in trajs if t["final_status"] == "timeout"]

        def avg_steps(ts):
            if not ts:
                return 0.0
            return sum(len(t["steps"]) for t in ts) / len(ts)

        # Tool usage distribution
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

        retry_rate = retry_steps / total_steps if total_steps else 0
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
            retry_rate=retry_rate,
            correction_success_rate=self._correction_success_rate(trajs),
            termination_reasons=dict(termination_counts),
        )

    def _correction_success_rate(self, trajs: list[dict]) -> float:
        """Of all retry steps, what fraction was followed by a non-error step?"""
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

    def failure_taxonomy(self, experiment_id: str) -> list[TaxonomyResult]:
        """Classify all failed trajectories into failure categories."""
        trajs = self._load(experiment_id)
        failed = [t for t in trajs if t["final_status"] != "success"]

        if not failed:
            return []

        category_counts: Counter = Counter()
        category_examples: dict[str, list[str]] = defaultdict(list)

        for traj in failed:
            for category, rule_fn in _TAXONOMY_RULES:
                if rule_fn(traj):
                    category_counts[category] += 1
                    if len(category_examples[category]) < 3:
                        category_examples[category].append(traj["task_id"])
                    break

        total = len(failed)
        results = []
        for category, count in category_counts.most_common():
            results.append(TaxonomyResult(
                category=category,
                count=count,
                fraction=count / total,
                example_task_ids=category_examples[category],
            ))
        return results

    # -----------------------------------------------------------------------
    # LLM-as-Critic taxonomy
    # -----------------------------------------------------------------------

    _LLM_SYSTEM_PROMPT = """\
You are an expert evaluator analyzing why a coding agent failed a task.
After your analysis, output ONLY a JSON object as your final answer (no markdown, no extra text after the JSON).

Dimension 1 — goal_alignment:
  "correct"  — agent worked toward the right solution but made execution mistakes
  "deviated" — agent misunderstood the task or went in the wrong direction

Dimension 2 — execution_issue (pick ONE):
  "logic"     — code logic is wrong (algorithm, condition, edge case)
  "tool"      — tool call failed (bad args, path, command not found)
  "self_eval" — agent thought it succeeded but solution is actually wrong
  "planning"  — agent lost track of goals, looped, or hit step limit
  "none"      — unclear

End your response with this JSON block:
{"goal_alignment": "...", "execution_issue": "...", "explanation": "one sentence", "fixable_by_more_steps": true_or_false}"""

    async def _classify_one(self, traj: dict) -> LLMTaxonomyResult:
        """Call LLM to classify a single failed trajectory."""
        from coder_agent.core.llm_client import LLMClient

        steps = traj.get("steps", [])
        step_summaries = []
        for i, s in enumerate(steps):
            thought = (s.get("thought") or "")[:200]
            obs = (s.get("observation") or "")[:300]
            err = s.get("error_type") or ""
            action = (s.get("action") or {})
            tool = action.get("tool", "")
            step_summaries.append(
                f"Step {i+1}: tool={tool or 'none'} err={err or 'none'}\n"
                f"  thought: {thought}\n"
                f"  observation: {obs}"
            )

        user_content = (
            f"Task: {traj.get('task_id', 'unknown')}\n"
            f"Final status: {traj.get('final_status', 'unknown')}\n"
            f"Total steps: {len(steps)}\n\n"
            + "\n".join(step_summaries)
        )

        client = LLMClient()
        result = await client.chat(
            messages=[{"role": "user", "content": user_content}],
            system=self._LLM_SYSTEM_PROMPT,
            tools=[],
            model=cfg.model.name,
            max_tokens=1024,
            temperature=0.0,
        )

        raw = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                raw = block["text"].strip()
                break

        # Parse JSON — find the LAST {...} block in the output (after <think> chains)
        import re
        parsed = {}
        # Find all JSON-like blocks, take the last one
        matches = list(re.finditer(r"\{[^{}]*\}", raw, re.DOTALL))
        for m in reversed(matches):
            try:
                parsed = json.loads(m.group())
                if "goal_alignment" in parsed or "execution_issue" in parsed:
                    break
            except json.JSONDecodeError:
                continue

        return LLMTaxonomyResult(
            task_id=traj.get("task_id", "unknown"),
            goal_alignment=parsed.get("goal_alignment", "unknown"),
            execution_issue=parsed.get("execution_issue", "unknown"),
            explanation=parsed.get("explanation", raw[:200]),
            fixable_by_more_steps=bool(parsed.get("fixable_by_more_steps", False)),
            final_status=traj.get("final_status", "unknown"),
            steps_used=len(steps),
        )

    def failure_taxonomy_llm(self, experiment_id: str) -> list[LLMTaxonomyResult]:
        """LLM-as-Critic two-dimensional classification of all failed trajectories."""
        trajs = self._load(experiment_id)
        failed = [t for t in trajs if t["final_status"] != "success"]
        if not failed:
            return []

        async def _run_all():
            results = []
            for traj in failed:
                print(f"  Classifying {traj.get('task_id', '?')}...", flush=True)
                r = await self._classify_one(traj)
                results.append(r)
            return results

        return asyncio.run(_run_all())

    def print_llm_taxonomy(self, results: list[LLMTaxonomyResult]) -> None:
        """Print a two-dimensional breakdown of LLM-classified failures."""
        if not results:
            print("No failed trajectories to classify.")
            return

        print(f"\n{'='*60}")
        print("LLM-as-Critic Failure Taxonomy")
        print(f"{'='*60}")
        print(f"Classified {len(results)} failed trajectories\n")

        # Dimension 1: goal alignment
        align_counts: Counter = Counter(r.goal_alignment for r in results)
        print("Goal Alignment:")
        for val, cnt in align_counts.most_common():
            bar = "█" * int(cnt / len(results) * 20)
            print(f"  {val:<12} {cnt:>3} ({cnt/len(results):.1%}) {bar}")

        print()

        # Dimension 2: execution issue
        issue_counts: Counter = Counter(r.execution_issue for r in results)
        print("Execution Issue:")
        for val, cnt in issue_counts.most_common():
            bar = "█" * int(cnt / len(results) * 20)
            print(f"  {val:<12} {cnt:>3} ({cnt/len(results):.1%}) {bar}")

        print()

        # Fixable breakdown
        fixable = sum(1 for r in results if r.fixable_by_more_steps)
        print(f"Fixable by more steps: {fixable}/{len(results)} ({fixable/len(results):.1%})")

        print()
        print("Per-task breakdown:")
        for r in results:
            fixable_tag = "[fixable]" if r.fixable_by_more_steps else ""
            print(f"  {r.task_id:<30} align={r.goal_alignment:<8} issue={r.execution_issue:<10} {fixable_tag}")
            print(f"    → {r.explanation}")

    def print_report(self, stats: TrajectoryStats, taxonomy: list[TaxonomyResult]) -> None:
        """Print a human-readable analysis report."""
        print(f"\n{'='*60}")
        print(f"Trajectory Analysis: {stats.experiment_id}")
        print(f"{'='*60}")
        print(f"Total tasks:   {stats.total_trajectories}")
        print(f"  Success:     {stats.success_count} ({stats.success_count/max(stats.total_trajectories,1):.1%})")
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
            for t in taxonomy:
                bar = "█" * int(t.fraction * 20)
                print(f"  {t.category:<20} {t.count:>3} ({t.fraction:.1%}) {bar}")
                if t.example_task_ids:
                    print(f"    examples: {', '.join(t.example_task_ids)}")

    def compare_experiments(self, experiment_ids: list[str]) -> None:
        """Print a side-by-side comparison of multiple experiments."""
        print(f"\n{'='*70}")
        print("Experiment Comparison")
        print(f"{'='*70}")
        header = f"{'Metric':<30}" + "".join(f"{eid:>10}" for eid in experiment_ids)
        print(header)
        print("-" * len(header))

        all_stats = {eid: self.compute_statistics(eid) for eid in experiment_ids}

        metrics = [
            ("Success Rate", lambda s: f"{s.success_count/max(s.total_trajectories,1):.1%}"),
            ("Avg Steps",    lambda s: f"{s.avg_steps_all:.1f}"),
            ("Retry Rate",   lambda s: f"{s.retry_rate:.1%}"),
            ("Correction%",  lambda s: f"{s.correction_success_rate:.1%}"),
            ("Avg Tokens",   lambda s: f"{s.avg_tokens:.0f}"),
        ]

        for name, fn in metrics:
            row = f"{name:<30}"
            for eid in experiment_ids:
                row += f"{fn(all_stats[eid]):>10}"
            print(row)
