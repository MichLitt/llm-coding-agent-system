from pathlib import Path

import click

from coder_agent.config import cfg
from coder_agent.memory.manager import MemoryManager


@click.command(name="memory")
@click.option("--project", default=".", type=click.Path(), help="Project directory (default: current workspace)")
def memory_command(project: str) -> None:
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
        for task in tasks:
            status = "OK" if task["success"] else "ERR"
            click.echo(f"  {status} [{task['created_at'][:19]}] {task['description'][:70]} ({task['steps']} steps)")
    else:
        click.echo("No task history found.")

    experiments = mem.list_experiments()
    if experiments:
        click.echo(f"\nExperiments ({len(experiments)}):")
        for experiment in experiments:
            click.echo(f"  {experiment['experiment_id']} @ {experiment['timestamp'][:19]}")
    mem.close()
