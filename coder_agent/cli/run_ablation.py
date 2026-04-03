"""Standalone CLI for running the C1–C6 ablation experiment suite.

Usage:
    uv run python -m coder_agent.cli.run_ablation --benchmark custom
    uv run python -m coder_agent.cli.run_ablation --benchmark humaneval --limit 20
    uv run python -m coder_agent.cli.run_ablation --presets C1,C3,C6
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click

from coder_agent.config import cfg
from coder_agent.eval.ablation import (
    PRESET_SEQUENCE,
    AblationRunner,
    compute_feature_deltas,
    print_delta_table,
    write_ablation_report,
)
from coder_agent.eval.runner import EvalRunner
from coder_agent.memory.trajectory import TrajectoryStore

from .factory import CONFIG_PRESETS, make_agent


@click.command(name="run_ablation")
@click.option(
    "--benchmark",
    type=click.Choice(["custom", "humaneval", "mbpp"]),
    default="custom",
    show_default=True,
    help="Benchmark suite to run ablation against.",
)
@click.option(
    "--presets",
    default=",".join(PRESET_SEQUENCE),
    show_default=True,
    help="Comma-separated list of presets to include (e.g. C1,C3,C6).",
)
@click.option(
    "--output",
    default=None,
    type=click.Path(),
    help="Directory for results JSON/JSONL artifacts (default: cfg.eval.output_dir).",
)
@click.option(
    "--report-dir",
    default=None,
    type=click.Path(),
    help="Directory to write the markdown report (default: <project_root>/report/).",
)
@click.option(
    "--limit",
    default=0,
    type=int,
    show_default=True,
    help="Limit number of tasks per config (0 = all).",
)
@click.option("--resume", is_flag=True, help="Resume from checkpoints.")
@click.option(
    "--experiment-config",
    default=None,
    help='JSON object passed to make_agent() as runtime experiment config, e.g. \'{"doom_loop_threshold": 3}\'.',
)
@click.option(
    "--version",
    default="v0.4.1",
    show_default=True,
    help="Version string for the output report filename.",
)
def run_ablation_command(
    benchmark: str,
    presets: str,
    output: str | None,
    report_dir: str | None,
    limit: int,
    resume: bool,
    experiment_config: str | None,
    version: str,
) -> None:
    """Run the C1–C6 ablation experiment and report per-feature contribution deltas."""
    selected_presets = tuple(p.strip() for p in presets.split(",") if p.strip())
    unknown = [p for p in selected_presets if p not in CONFIG_PRESETS]
    if unknown:
        raise click.BadParameter(
            f"Unknown preset(s): {unknown}. Valid presets: {sorted(CONFIG_PRESETS)}",
            param_hint="--presets",
        )

    output_dir = Path(output) if output else cfg.eval.output_dir
    rpt_dir = (
        Path(report_dir)
        if report_dir
        else Path(__file__).resolve().parents[2] / "report"
    )
    tstore = TrajectoryStore(cfg.eval.trajectory_dir)
    parsed_experiment_config = _parse_experiment_config(experiment_config)

    def agent_factory(agent_cfg: dict):
        return make_agent(
            agent_cfg,
            experiment_id="ablation",
            trajectory_store=tstore,
            config_label=_resolve_config_label(agent_cfg),
            experiment_config=parsed_experiment_config,
        )

    runner = EvalRunner(agent_factory=agent_factory, output_dir=output_dir)

    # Load tasks (for MBPP, pass limit to avoid downloading full 374 when limit is set)
    tasks = _load_tasks(benchmark, limit=limit)
    if limit > 0 and benchmark != "mbpp":
        tasks = tasks[:limit]

    click.echo(f"Loaded {len(tasks)} {benchmark} task(s)")
    click.echo(f"Running ablation for presets: {', '.join(selected_presets)}")

    ablation_runner = AblationRunner(eval_runner=runner)
    report = ablation_runner.run(
        tasks,
        CONFIG_PRESETS,
        presets=selected_presets,
        report_label="ablation",
        benchmark_name=benchmark,
        resume=resume,
        verbose=True,
    )

    marginal_deltas = compute_feature_deltas(report, mode="marginal")
    try:
        cumulative_deltas = compute_feature_deltas(report, mode="cumulative")
    except KeyError:
        cumulative_deltas = []
        click.echo("(Cumulative deltas skipped: C1 baseline not in selected presets)")

    if marginal_deltas:
        print_delta_table(marginal_deltas, title="=== Marginal Deltas (each config vs direct predecessor) ===")
    if cumulative_deltas:
        print_delta_table(cumulative_deltas, title="=== Cumulative Deltas (each config vs C1 baseline) ===")

    report_path = write_ablation_report(
        marginal_deltas,
        cumulative_deltas,
        report,
        output_dir=rpt_dir,
        benchmark=benchmark,
        version=version,
    )
    click.echo(f"\nReport written to: {report_path}")


def _load_tasks(benchmark: str, limit: int = 0):
    if benchmark == "humaneval":
        from coder_agent.eval.benchmarks.humaneval import HumanEvalBenchmark
        from coder_agent.eval.runner import TaskSpec

        he = HumanEvalBenchmark()
        he_tasks = he.load()
        return [
            TaskSpec(
                task_id=t.task_id.replace("/", "_"),
                description=he.build_agent_prompt(t),
                difficulty="medium",
                verification=[],
                verification_contract={"mode": "humaneval_official", "max_attempts": 2},
                metadata={
                    "benchmark": "humaneval",
                    "prompt": t.prompt,
                    "entry_point": t.entry_point,
                    "test": t.test,
                },
            )
            for t in he_tasks
        ]
    elif benchmark == "mbpp":
        from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark

        mb = MBPPBenchmark()
        mb_tasks = mb.load(limit=limit or 60)
        return [mb.to_task_spec(t) for t in mb_tasks]
    else:
        from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks

        return load_custom_tasks()


def _parse_experiment_config(raw_value: str | None) -> dict | None:
    if raw_value is None:
        return None
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"Invalid JSON for --experiment-config: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise click.BadParameter("--experiment-config must decode to a JSON object.")
    return parsed


def _resolve_config_label(agent_cfg: dict) -> str | None:
    for label, preset_cfg in CONFIG_PRESETS.items():
        if preset_cfg == agent_cfg:
            return label
    return None


def main() -> None:
    run_ablation_command()


if __name__ == "__main__":
    main()
