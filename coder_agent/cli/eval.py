import json
from pathlib import Path

import click

from coder_agent.config import cfg
from coder_agent.core.agent import Agent
from coder_agent.eval.runner import EvalRunner, TaskSpec
from coder_agent.memory.trajectory import TrajectoryStore

from .factory import (
    BENCHMARK_CANDIDATE_PRESETS,
    CONFIG_PRESETS,
    invalid_preset_labels,
    make_agent,
    resolve_agent_config,
)


@click.command(name="eval")
@click.option("--benchmark", type=click.Choice(["humaneval", "custom"]), default="custom", help="Which benchmark to run")
@click.option("--task-dir", default=None, type=click.Path(), help="Directory with custom task YAML (default: built-in)")
@click.option("--output", default=None, type=click.Path(), help="Output directory for results")
@click.option("--limit", default=0, type=int, help="Limit number of tasks (0 = all)")
@click.option("--task-id", "task_ids", multiple=True, help="Run only the specified task_id values. Can be repeated.")
@click.option(
    "--compare",
    default=None,
    help=(
        "Comma-separated config labels to compare. "
        f"For the active benchmark candidate presets, use {','.join(BENCHMARK_CANDIDATE_PRESETS)}."
    ),
)
@click.option("--preset", type=click.Choice(["default", "C1", "C2", "C3", "C4", "C5", "C6"]), default="default", help="Single-run config preset to use")
@click.option("--resume", is_flag=True, help="Resume from checkpoint files for this config label")
@click.option("--config-label", default="eval", help="Label for this run (used in output filenames)")
@click.option(
    "--experiment-config",
    default=None,
    help='JSON object passed to make_agent() as runtime experiment config, e.g. \'{"memory_lookup_mode": "similarity"}\'.',
)
def eval_command(
    benchmark: str,
    task_dir: str | None,
    output: str | None,
    limit: int,
    task_ids: tuple[str, ...],
    compare: str | None,
    preset: str,
    resume: bool,
    config_label: str,
    experiment_config: str | None,
) -> None:
    if compare and preset != "default":
        raise click.UsageError("--compare and --preset are mutually exclusive")

    output_dir = Path(output) if output else cfg.eval.output_dir
    tstore = TrajectoryStore(cfg.eval.trajectory_dir)
    parsed_experiment_config = _parse_experiment_config(experiment_config)

    def agent_factory(agent_cfg: dict) -> Agent:
        return make_agent(
            agent_cfg,
            experiment_id=config_label,
            trajectory_store=tstore,
            config_label=config_label,
            experiment_config=parsed_experiment_config,
        )

    runner = EvalRunner(agent_factory=agent_factory, output_dir=output_dir)

    if benchmark == "humaneval":
        from coder_agent.eval.benchmarks.humaneval import HumanEvalBenchmark

        he = HumanEvalBenchmark()
        he_tasks = he.load(limit=limit)
        tasks = [
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
        click.echo(f"Loaded {len(tasks)} HumanEval tasks")
    else:
        from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks

        yaml_path = Path(task_dir) / "tasks.yaml" if task_dir else None
        tasks = load_custom_tasks(yaml_path) if yaml_path else load_custom_tasks()

    if task_ids:
        wanted = list(dict.fromkeys(task_ids))
        available = {task.task_id for task in tasks}
        missing = [task_id for task_id in wanted if task_id not in available]
        if missing:
            raise click.UsageError("Unknown task id(s): " + ", ".join(missing))
        wanted_set = set(wanted)
        tasks = [task for task in tasks if task.task_id in wanted_set]

    if limit > 0:
        tasks = tasks[:limit]
    click.echo(f"Loaded {len(tasks)} {benchmark} tasks")

    if compare:
        labels = [label.strip() for label in compare.split(",")]
        invalid_labels = invalid_preset_labels(labels)
        if invalid_labels:
            raise click.UsageError(
                "Unknown preset label(s): "
                + ", ".join(invalid_labels)
                + ". Valid presets: "
                + ", ".join(sorted(CONFIG_PRESETS))
            )
        label_map = {label: f"{config_label}_{label}" if config_label else label for label in labels}
        configs = {label_map[label]: resolve_agent_config(label) for label in labels}

        def agent_factory_compare(agent_cfg: dict) -> Agent:
            experiment_id = next(
                (name for name, cfg_item in configs.items() if cfg_item == agent_cfg),
                config_label,
            )
            return make_agent(
                agent_cfg,
                experiment_id=experiment_id,
                trajectory_store=tstore,
                config_label=experiment_id,
                experiment_config=parsed_experiment_config,
            )

        runner_cmp = EvalRunner(agent_factory=agent_factory_compare, output_dir=output_dir)
        runner_cmp.compare_configs(
            tasks,
            configs,
            report_label=config_label,
            experiment_config=parsed_experiment_config,
            benchmark_name=benchmark,
            resume=resume,
        )
        return

    runner.run_suite(
        tasks,
        config_label=config_label,
        agent_config=resolve_agent_config(preset),
        experiment_config=parsed_experiment_config,
        benchmark_name=benchmark,
        preset=preset,
        resume=resume,
    )


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
