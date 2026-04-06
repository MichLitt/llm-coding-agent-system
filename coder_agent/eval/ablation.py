"""Ablation experiment framework for the Coder-Agent.

Orchestrates runs of the existing C1–C6 CONFIG_PRESETS via EvalRunner.compare_configs(),
then computes per-feature metric deltas in two modes:

  marginal   — each config compared against its direct predecessor (the single feature added)
  cumulative — each config compared against C1 (total benefit vs no-feature baseline)

To add a new preset (e.g. C7 with a new feature):
    1. Add "C7": {...} to CONFIG_PRESETS in factory.py
    2. Append "C7" to PRESET_SEQUENCE
    3. Add FEATURE_ADDED["C7"] = "+new_feature"
    4. Append ("C7", "<reference>") to MARGINAL_PAIRS
    AblationRunner, compute_feature_deltas, and print_delta_table need no changes.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from coder_agent.eval.metrics import MetricsSummary
from coder_agent.eval.runner import ComparisonReport, EvalRunner, TaskSpec


# ---------------------------------------------------------------------------
# Experiment design constants — edit these when adding new presets
# ---------------------------------------------------------------------------

PRESET_SEQUENCE: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5", "C6")

# Human-readable label for what each preset adds relative to C1
FEATURE_ADDED: dict[str, str] = {
    "C1": "baseline",
    "C2": "+planning=react",
    "C3": "+correction",
    "C4": "+memory",
    "C5": "+checklist",
    "C6": "+verification_gate",
}

# Marginal pairs: (config, reference) — each pair isolates one feature
# C5 and C6 both branch from C3 (not C4) because they each add one orthogonal feature
MARGINAL_PAIRS: tuple[tuple[str, str], ...] = (
    ("C2", "C1"),  # +planning=react
    ("C3", "C2"),  # +correction
    ("C4", "C3"),  # +memory
    ("C5", "C3"),  # +checklist (branches from C3, not C4)
    ("C6", "C3"),  # +verification_gate (branches from C3, not C4)
)


# ---------------------------------------------------------------------------
# FeatureDelta dataclass
# ---------------------------------------------------------------------------

@dataclass
class FeatureDelta:
    """Metric delta for a single (config, reference) comparison pair."""

    config: str                           # e.g. "C3"
    reference: str                        # e.g. "C2" (marginal) or "C1" (cumulative)
    feature_added: str                    # human label: "+correction"
    benchmark_pass_rate_delta: float      # config - reference (positive = feature helps)
    strict_success_rate_delta: float
    efficiency_score_delta: float
    retry_cost_delta: float               # negative = feature reduces retry overhead
    avg_steps_delta: float
    config_benchmark_pass_rate: float
    reference_benchmark_pass_rate: float


# ---------------------------------------------------------------------------
# compute_feature_deltas
# ---------------------------------------------------------------------------

def compute_feature_deltas(
    report: ComparisonReport,
    *,
    mode: str = "marginal",
    baseline: str = "C1",
) -> list[FeatureDelta]:
    """Compute per-feature contribution deltas from a ComparisonReport.

    Args:
        report: ComparisonReport returned by EvalRunner.compare_configs().
        mode: "marginal" uses MARGINAL_PAIRS (each delta = config − direct reference).
              "cumulative" compares every config against the baseline.
        baseline: Config label used as the all-off reference for cumulative mode.

    Returns:
        List of FeatureDelta objects. Missing configs are silently skipped.

    Raises:
        KeyError: if baseline is not present in report.summaries (cumulative mode only).
        ValueError: if mode is not "marginal" or "cumulative".
    """
    if mode not in ("marginal", "cumulative"):
        raise ValueError(f"mode must be 'marginal' or 'cumulative', got {mode!r}")

    summaries = report.summaries

    if mode == "cumulative":
        if baseline not in summaries:
            raise KeyError(
                f"Baseline '{baseline}' not found in report.summaries. "
                f"Available: {sorted(summaries)}"
            )
        base_s = summaries[baseline]
        deltas: list[FeatureDelta] = []
        for config in PRESET_SEQUENCE:
            if config == baseline or config not in summaries:
                continue
            s = summaries[config]
            deltas.append(_make_delta(config, baseline, s, base_s))
        return deltas

    # marginal mode
    deltas = []
    for config, ref in MARGINAL_PAIRS:
        if config not in summaries or ref not in summaries:
            continue
        deltas.append(_make_delta(config, ref, summaries[config], summaries[ref]))
    return deltas


def _make_delta(
    config: str,
    reference: str,
    s: MetricsSummary,
    ref_s: MetricsSummary,
) -> FeatureDelta:
    return FeatureDelta(
        config=config,
        reference=reference,
        feature_added=FEATURE_ADDED.get(config, config),
        benchmark_pass_rate_delta=s.benchmark_pass_rate - ref_s.benchmark_pass_rate,
        strict_success_rate_delta=s.strict_success_rate - ref_s.strict_success_rate,
        efficiency_score_delta=s.efficiency_score - ref_s.efficiency_score,
        retry_cost_delta=s.retry_cost - ref_s.retry_cost,
        avg_steps_delta=s.avg_steps - ref_s.avg_steps,
        config_benchmark_pass_rate=s.benchmark_pass_rate,
        reference_benchmark_pass_rate=ref_s.benchmark_pass_rate,
    )


# ---------------------------------------------------------------------------
# print_delta_table
# ---------------------------------------------------------------------------

def print_delta_table(deltas: list[FeatureDelta], *, title: str = "") -> None:
    """Print a human-readable table of feature contribution deltas."""
    if title:
        print(f"\n{title}")
    header = (
        f"{'Config':<8} {'vs':<6} {'Feature':<26} "
        f"{'BenchΔ':>8} {'StrictΔ':>8} {'EffΔ':>10} {'RetryCostΔ':>12} {'AvgStepsΔ':>10}"
    )
    print(header)
    print("-" * len(header))
    for d in deltas:
        print(
            f"{d.config:<8} {d.reference:<6} {d.feature_added:<26} "
            f"{d.benchmark_pass_rate_delta:>+8.1%} "
            f"{d.strict_success_rate_delta:>+8.1%} "
            f"{d.efficiency_score_delta:>+10.4f} "
            f"{d.retry_cost_delta:>+12.1%} "
            f"{d.avg_steps_delta:>+10.1f}"
        )


# ---------------------------------------------------------------------------
# write_ablation_report
# ---------------------------------------------------------------------------

# Ablation matrix config table — mirrors factory.py CONFIG_PRESETS for reporting
_ABLATION_CONFIG_TABLE: dict[str, dict] = {
    "C1": {"correction": False, "memory": False, "checklist": False, "verification_gate": False, "planning_mode": "direct"},
    "C2": {"correction": False, "memory": False, "checklist": False, "verification_gate": False, "planning_mode": "react"},
    "C3": {"correction": True,  "memory": False, "checklist": False, "verification_gate": False, "planning_mode": "react"},
    "C4": {"correction": True,  "memory": True,  "checklist": False, "verification_gate": False, "planning_mode": "react"},
    "C5": {"correction": True,  "memory": False, "checklist": True,  "verification_gate": False, "planning_mode": "react"},
    "C6": {"correction": True,  "memory": False, "checklist": False, "verification_gate": True,  "planning_mode": "react"},
}


def write_ablation_report(
    marginal_deltas: list[FeatureDelta],
    cumulative_deltas: list[FeatureDelta],
    report: ComparisonReport,
    *,
    output_dir: Path,
    benchmark: str,
    version: str = "v0.4.1",
) -> Path:
    """Write a markdown ablation report to output_dir.

    Filename: IMPROVEMENT_REPORT_{version}.md
    Returns the Path of the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"IMPROVEMENT_REPORT_{version}.md"
    today = datetime.date.today().isoformat()

    lines: list[str] = [
        f"# Ablation Report {version}",
        "",
        f"> Date: {today}",
        f"> Benchmark: {benchmark}",
        f"> Scope: feature ablation study — contribution of each C1–C6 component",
        "",
        "---",
        "",
        "## Overview",
        "",
        "This report quantifies the contribution of each individual feature in the C1–C6",
        "configuration sequence by computing metric deltas between consecutive presets",
        "(marginal view) and against the C1 all-off baseline (cumulative view).",
        "",
        "---",
        "",
        "## Stage 1: Ablation Matrix",
        "",
        "| Preset | correction | memory | checklist | verification_gate | planning_mode | Feature Added |",
        "|--------|-----------|--------|-----------|-------------------|---------------|---------------|",
    ]

    for preset in PRESET_SEQUENCE:
        cfg = _ABLATION_CONFIG_TABLE.get(preset, {})
        lines.append(
            f"| {preset} "
            f"| {cfg.get('correction', False)} "
            f"| {cfg.get('memory', False)} "
            f"| {cfg.get('checklist', False)} "
            f"| {cfg.get('verification_gate', False)} "
            f"| {cfg.get('planning_mode', '-')} "
            f"| {FEATURE_ADDED.get(preset, '-')} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Stage 2: Metric Results",
        "",
        "| Config | N | BenchPass | CleanComplete | StrictSuccess | Efficiency | RetryCost | AvgSteps | AvgTokens |",
        "|--------|---|-----------|---------------|---------------|------------|-----------|----------|-----------|",
    ]

    for config in PRESET_SEQUENCE:
        s = report.summaries.get(config)
        if s is None:
            continue
        lines.append(
            f"| {config} | {s.n_tasks} "
            f"| {s.benchmark_pass_rate:.1%} "
            f"| {s.clean_completion_rate:.1%} "
            f"| {s.strict_success_rate:.1%} "
            f"| {s.efficiency_score:.4f} "
            f"| {s.retry_cost:.1%} "
            f"| {s.avg_steps:.1f} "
            f"| {s.avg_tokens:.0f} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Stage 3: Feature Contribution Deltas",
        "",
        "### Marginal Deltas (each config vs its direct predecessor)",
        "",
        "Delta = config_metric − reference_metric. Positive = feature improves the metric.",
        "",
        "| Config | vs | Feature | BenchΔ | StrictΔ | EfficiencyΔ | RetryCostΔ | AvgStepsΔ |",
        "|--------|----|---------|----|---------|-------------|------------|-----------|",
    ]
    for d in marginal_deltas:
        lines.append(
            f"| {d.config} | {d.reference} | {d.feature_added} "
            f"| {d.benchmark_pass_rate_delta:+.1%} "
            f"| {d.strict_success_rate_delta:+.1%} "
            f"| {d.efficiency_score_delta:+.4f} "
            f"| {d.retry_cost_delta:+.1%} "
            f"| {d.avg_steps_delta:+.1f} |"
        )

    lines += [
        "",
        "### Cumulative Deltas (each config vs C1 baseline)",
        "",
        "| Config | vs | Feature | BenchΔ | StrictΔ | EfficiencyΔ | RetryCostΔ | AvgStepsΔ |",
        "|--------|----|---------|----|---------|-------------|------------|-----------|",
    ]
    for d in cumulative_deltas:
        lines.append(
            f"| {d.config} | {d.reference} | {d.feature_added} "
            f"| {d.benchmark_pass_rate_delta:+.1%} "
            f"| {d.strict_success_rate_delta:+.1%} "
            f"| {d.efficiency_score_delta:+.4f} "
            f"| {d.retry_cost_delta:+.1%} "
            f"| {d.avg_steps_delta:+.1f} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Interpretation",
        "",
        "The marginal delta table shows the isolated contribution of each feature when added",
        "to the preceding configuration. C5 and C6 both branch from C3 (not C4) because they",
        "each add one orthogonal feature — checklist and verification_gate respectively — to",
        "the correction+react foundation.",
        "",
        "The cumulative delta table shows the total accumulated benefit of each preset relative",
        "to the C1 all-off baseline.",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"Ablation study completed on {today}. Benchmark: {benchmark}.",
        "Base framework: C1–C6 CONFIG_PRESETS from factory.py (no new presets added).",
        "To extend: add a new preset to CONFIG_PRESETS and append entries to",
        "PRESET_SEQUENCE, FEATURE_ADDED, and MARGINAL_PAIRS in ablation.py.",
        "",
    ]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# AblationRunner
# ---------------------------------------------------------------------------

class AblationRunner:
    """Thin adapter: runs selected presets via EvalRunner.compare_configs().

    Does not duplicate any task-execution logic — all checkpointing, manifests,
    timing, and metric aggregation remain in EvalRunner.compare_configs().
    """

    def __init__(self, eval_runner: EvalRunner) -> None:
        self.eval_runner = eval_runner

    def run(
        self,
        tasks: list[TaskSpec],
        preset_configs: dict[str, dict],
        *,
        presets: tuple[str, ...] = PRESET_SEQUENCE,
        report_label: str = "ablation",
        experiment_config: dict | None = None,
        benchmark_name: str = "",
        resume: bool = False,
        verbose: bool = True,
    ) -> ComparisonReport:
        """Run the ablation suite and return a ComparisonReport.

        Args:
            tasks: Task list to evaluate.
            preset_configs: CONFIG_PRESETS dict from factory.py.
            presets: Which presets to include (default: all C1–C6).
            report_label: Passed to EvalRunner.compare_configs().
            benchmark_name: Passed to EvalRunner.compare_configs().
            resume: Whether to resume from checkpoints.
            verbose: Whether to print per-task progress.

        Returns:
            ComparisonReport from EvalRunner.compare_configs().
        """
        configs = {p: preset_configs[p] for p in presets if p in preset_configs}
        return self.eval_runner.compare_configs(
            tasks,
            configs,
            report_label=report_label,
            experiment_config=experiment_config,
            benchmark_name=benchmark_name,
            resume=resume,
            verbose=verbose,
        )
