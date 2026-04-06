"""CLI entrypoint for LLM Coding Agent System."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click

from coder_agent.cli.analyze import analyze_command
from coder_agent.cli.chat import chat, run_chat_repl
from coder_agent.cli.eval import eval_command
from coder_agent.cli.factory import make_agent, make_session, make_trajectory_store, resolve_agent_config
from coder_agent.cli.memory import memory_command


@click.group(invoke_without_command=True)
@click.option("--model", default=None, help="Override model name from config.yaml for default chat mode")
@click.option("--no-memory", is_flag=True, help="Disable long-term memory for default chat mode")
@click.option("--llm-profile", default=None, help="Named LLM profile from config.yaml llm.profiles")
@click.pass_context
def cli(ctx: click.Context, model: str | None, no_memory: bool, llm_profile: str | None) -> None:
    """LLM Coding Agent System: ReAct-based AI coding assistant."""
    if ctx.invoked_subcommand is None:
        run_chat_repl(make_session(model=model, no_memory=no_memory, llm_profile=llm_profile), stdin=sys.stdin)


@cli.command()
@click.argument("task")
@click.option("--model", default=None, help="Override model name")
@click.option("--no-memory", is_flag=True, help="Disable long-term memory")
@click.option("--llm-profile", default=None, help="Named LLM profile from config.yaml llm.profiles")
@click.option("--trajectory-dir", default=None, type=click.Path(), help="Directory to save trajectories")
def run(task: str, model: str | None, no_memory: bool, llm_profile: str | None, trajectory_dir: str | None) -> None:
    agent = make_agent(
        model=model,
        no_memory=no_memory,
        llm_profile=llm_profile,
        trajectory_store=make_trajectory_store(trajectory_dir),
    )
    try:
        result = agent.run(task)
        click.echo(
            f"\n[{'OK' if result.success else 'ERR'}] "
            f"steps={result.steps} tools={','.join(result.tool_calls)}"
        )
    finally:
        if hasattr(agent, "close"):
            agent.close()


cli.add_command(chat)
cli.add_command(eval_command)
cli.add_command(memory_command)
cli.add_command(analyze_command)


def main() -> None:
    cli()


__all__ = [
    "chat",
    "cli",
    "main",
    "make_agent",
    "resolve_agent_config",
]


if __name__ == "__main__":
    main()
