"""Click CLI for LLM Coding Agent System.

Commands
--------
  coder-agent chat       Interactive chat mode
  coder-agent run        Single task mode
  coder-agent eval       Run evaluation benchmarks
  coder-agent memory     Show project memory
  coder-agent analyze    Analyze trajectory results
"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click

from coder_agent.config import cfg
from coder_agent.core.agent import Agent, build_tools
from coder_agent.core.llm_client import LLMClient
from coder_agent.memory.manager import MemoryManager
from coder_agent.memory.trajectory import TrajectoryStore


CONFIG_PRESETS: dict[str, dict] = {
    "default": {},
    "C1": {"correction": False, "memory": False, "planning_mode": "direct"},
    "C2": {"correction": False, "memory": False, "planning_mode": "react"},
    "C3": {"correction": True, "memory": False, "planning_mode": "react"},
    "C4": {"correction": True, "memory": True, "planning_mode": "react"},
    "C5": {"correction": True, "memory": True, "planning_mode": "react", "checklist": True},
    "C6": {"correction": True, "memory": False, "planning_mode": "react", "verification_gate": True},
}


def resolve_agent_config(preset: str) -> dict:
    """Return a copy of the agent config for a named preset."""
    return dict(CONFIG_PRESETS.get(preset, {}))


def _make_agent(
    agent_config: dict | None = None,
    experiment_id: str = "default",
    trajectory_store: TrajectoryStore | None = None,
) -> Agent:
    """Factory function to create an Agent with the given config overrides."""
    agent_config = agent_config or {}
    memory = None
    if agent_config.get("memory", cfg.agent.enable_memory):
        memory = MemoryManager(cfg.agent.memory_db_path)

    return Agent(
        tools=build_tools(),
        client=LLMClient(),
        memory=memory,
        trajectory_store=trajectory_store,
        experiment_id=experiment_id,
        experiment_config=agent_config,
    )


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option("0.2.0", prog_name="LLM Coding Agent System")
def cli():
    """LLM Coding Agent System: ReAct-based AI coding assistant."""


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--model", default=None, help="Override model name from config.yaml")
@click.option("--no-memory", is_flag=True, help="Disable long-term memory")
def chat(model: str | None, no_memory: bool):
    """Start an interactive chat session with the agent."""
    if model:
        cfg.model.name = model

    memory = None
    if not no_memory and cfg.agent.enable_memory:
        memory = MemoryManager(cfg.agent.memory_db_path)

    agent = Agent(tools=build_tools(), client=LLMClient(), memory=memory)
    click.echo("LLM Coding Agent System ready. Type 'exit' to quit.\n")

    while True:
        try:
            task = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if task.lower() in ("exit", "quit"):
            break
        if task:
            agent.run(task)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("task")
@click.option("--model", default=None, help="Override model name")
@click.option("--no-memory", is_flag=True, help="Disable long-term memory")
@click.option("--trajectory-dir", default=None, type=click.Path(), help="Directory to save trajectories")
def run(task: str, model: str | None, no_memory: bool, trajectory_dir: str | None):
    """Run a single task and exit."""
    if model:
        cfg.model.name = model

    tstore = None
    if trajectory_dir:
        tstore = TrajectoryStore(Path(trajectory_dir))

    memory = None
    if not no_memory and cfg.agent.enable_memory:
        memory = MemoryManager(cfg.agent.memory_db_path)

    agent = Agent(tools=build_tools(), client=LLMClient(), memory=memory, trajectory_store=tstore)
    result = agent.run(task)
    click.echo(f"\n[{'✓' if result.success else '✗'}] steps={result.steps} tools={','.join(result.tool_calls)}")


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--benchmark", type=click.Choice(["humaneval", "custom"]), default="custom",
              help="Which benchmark to run")
@click.option("--task-dir", default=None, type=click.Path(), help="Directory with custom task YAML (default: built-in)")
@click.option("--output", default=None, type=click.Path(), help="Output directory for results")
@click.option("--limit", default=0, type=int, help="Limit number of tasks (0 = all)")
@click.option("--compare", default=None, help="Comma-separated config labels to compare: C1,C2,C3,C4")
@click.option("--preset", type=click.Choice(["default", "C1", "C2", "C3", "C4", "C5", "C6"]), default="default",
              help="Single-run config preset to use")
@click.option("--resume", is_flag=True, help="Resume from checkpoint files for this config label")
@click.option("--config-label", default="eval", help="Label for this run (used in output filenames)")
def eval(
    benchmark: str,
    task_dir: str | None,
    output: str | None,
    limit: int,
    compare: str | None,
    preset: str,
    resume: bool,
    config_label: str,
):
    """Run evaluation benchmarks."""
    from coder_agent.eval.runner import EvalRunner

    if compare and preset != "default":
        raise click.UsageError("--compare and --preset are mutually exclusive")

    output_dir = Path(output) if output else cfg.eval.output_dir
    tstore = TrajectoryStore(cfg.eval.trajectory_dir)

    def agent_factory(agent_cfg: dict) -> Agent:
        return _make_agent(agent_cfg, experiment_id=config_label, trajectory_store=tstore)

    runner = EvalRunner(agent_factory=agent_factory, output_dir=output_dir)

    # Load tasks
    if benchmark == "humaneval":
        from coder_agent.eval.benchmarks.humaneval import HumanEvalBenchmark
        from coder_agent.eval.runner import TaskSpec
        he = HumanEvalBenchmark()
        he_tasks = he.load(limit=limit)
        tasks = [
            TaskSpec(
                task_id=t.task_id.replace("/", "_"),
                description=he.build_agent_prompt(t),
                difficulty="medium",
                verification=[],  # HumanEval uses custom evaluation logic
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
        if limit > 0:
            tasks = tasks[:limit]
        click.echo(f"Loaded {len(tasks)} custom tasks")

    # Run
    if compare:
        labels = [l.strip() for l in compare.split(",")]
        label_map = {
            label: f"{config_label}_{label}" if config_label else label
            for label in labels
        }
        configs = {
            label_map[label]: resolve_agent_config(label)
            for label in labels
        }

        def agent_factory_compare(agent_cfg: dict) -> Agent:
            experiment_id = next(
                (name for name, cfg_item in configs.items() if cfg_item == agent_cfg),
                config_label,
            )
            return _make_agent(agent_cfg, experiment_id=experiment_id, trajectory_store=tstore)

        runner_cmp = EvalRunner(agent_factory=agent_factory_compare, output_dir=output_dir)
        runner_cmp.compare_configs(
            tasks,
            configs,
            report_label=config_label,
            benchmark_name=benchmark,
            resume=resume,
        )
    else:
        runner.run_suite(
            tasks,
            config_label=config_label,
            agent_config=resolve_agent_config(preset),
            benchmark_name=benchmark,
            preset=preset,
            resume=resume,
        )


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--project", default=".", type=click.Path(), help="Project directory (default: current workspace)")
def memory(project: str):
    """Show stored memory for a project."""
    mem = MemoryManager(cfg.agent.memory_db_path)
    workspace = Path(project).resolve()
    project_id = mem.get_or_create_project(workspace)

    tasks = mem.get_recent_tasks(project_id, n=10)
    notes = mem.get_notes(project_id)

    click.echo(f"\nProject: {workspace}")
    click.echo(f"Project ID: {project_id}")

    if notes:
        click.echo(f"\nNotes:\n{notes}")

    if tasks:
        click.echo(f"\nRecent tasks ({len(tasks)}):")
        for t in tasks:
            status = "✓" if t["success"] else "✗"
            click.echo(f"  {status} [{t['created_at'][:19]}] {t['description'][:70]} ({t['steps']} steps)")
    else:
        click.echo("No task history found.")

    experiments = mem.list_experiments()
    if experiments:
        click.echo(f"\nExperiments ({len(experiments)}):")
        for e in experiments:
            click.echo(f"  {e['experiment_id']} @ {e['timestamp'][:19]}")

    mem.close()


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("experiment_id")
@click.option("--compare", default=None, help="Comma-separated experiment IDs to compare")
@click.option("--llm-taxonomy", is_flag=True, help="Use LLM-as-Critic for two-dimensional failure classification")
def analyze(experiment_id: str, compare: str | None, llm_taxonomy: bool):
    """Analyze trajectory results for an experiment."""
    import json

    from coder_agent.eval.analysis import TrajectoryAnalyzer

    analyzer = TrajectoryAnalyzer()

    if compare:
        ids = [experiment_id] + [s.strip() for s in compare.split(",")]
        analyzer.compare_experiments(ids)
    else:
        manifest_path = cfg.eval.output_dir / f"{experiment_id}_comparison_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            analyzer.compare_experiments(manifest.get("experiments", []))
            return

        stats = analyzer.compute_statistics(experiment_id)
        taxonomy = analyzer.failure_taxonomy(experiment_id)
        analyzer.print_report(stats, taxonomy)

        if llm_taxonomy:
            click.echo("\nRunning LLM-as-Critic classification (this may take a moment)...")
            llm_results = analyzer.failure_taxonomy_llm(experiment_id)
            analyzer.print_llm_taxonomy(llm_results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    cli()


if __name__ == "__main__":
    main()
