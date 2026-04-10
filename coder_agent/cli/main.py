"""CLI entrypoint for LLM Coding Agent System."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import click

from coder_agent.cli.analyze import analyze_command
from coder_agent.cli.chat import chat, run_chat_repl
from coder_agent.cli.eval import eval_command
from coder_agent.cli.factory import (
    make_agent,
    make_run_state_store,
    make_session,
    make_trajectory_store,
    resolve_agent_config,
)
from coder_agent.cli.memory import memory_command
from coder_agent.cli.serve import serve_command
from coder_agent.config import cfg


def _summarize_text(value: Any, *, limit: int = 72) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _format_timestamp(timestamp: Any) -> str:
    if timestamp in (None, ""):
        return "-"
    return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M:%S")


def _require_run_state_store():
    if not cfg.agent.enable_run_state:
        raise click.ClickException("Run-state persistence is disabled.")
    return make_run_state_store()


def _print_run_detail(run: dict[str, Any], checkpoint: dict[str, Any] | None) -> None:
    click.echo(f"run_id={run['run_id']}")
    click.echo(f"status={run.get('status') or '-'}")
    click.echo(f"task={run.get('task_description') or '-'}")
    click.echo(f"steps={run.get('total_steps', 0)} tools={run.get('total_tool_calls', 0)}")
    click.echo(f"created_at={_format_timestamp(run.get('created_at'))}")
    click.echo(f"started_at={_format_timestamp(run.get('started_at'))}")
    click.echo(f"finished_at={_format_timestamp(run.get('finished_at'))}")
    click.echo(f"termination_reason={run.get('termination_reason') or '-'}")
    if checkpoint is None:
        click.echo("latest_checkpoint=none")
        return
    click.echo(f"latest_checkpoint.step={checkpoint['step_index']}")
    click.echo(f"latest_checkpoint.thought={_summarize_text(checkpoint.get('thought_text'))}")
    click.echo(f"latest_checkpoint.observation={_summarize_text(checkpoint.get('observation_text'), limit=120)}")
    click.echo(f"latest_checkpoint.recorded_at={_format_timestamp(checkpoint.get('recorded_at'))}")


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
@click.argument("task", required=False)
@click.option("--model", default=None, help="Override model name")
@click.option("--no-memory", is_flag=True, help="Disable long-term memory")
@click.option("--llm-profile", default=None, help="Named LLM profile from config.yaml llm.profiles")
@click.option("--trajectory-dir", default=None, type=click.Path(), help="Directory to save trajectories")
@click.option("--run-id", default=None, help="Use a specific run_id for this run")
@click.option("--resume", "resume_run_id", default=None, help="Resume a persisted run by run_id")
def run(
    task: str,
    model: str | None,
    no_memory: bool,
    llm_profile: str | None,
    trajectory_dir: str | None,
    run_id: str | None,
    resume_run_id: str | None,
) -> None:
    if run_id and resume_run_id:
        raise click.UsageError("--run-id and --resume are mutually exclusive")
    if not resume_run_id and not task:
        raise click.UsageError("TASK is required unless --resume is used")

    effective_run_id = resume_run_id or run_id
    run_state_store = None
    effective_task = task
    resume_target = None
    if cfg.agent.enable_run_state:
        run_state_store = make_run_state_store()
    if resume_run_id:
        if run_state_store is None:
            raise click.ClickException("Run-state persistence is disabled; resume is unavailable.")
        resume_target = run_state_store.get_resume_target(resume_run_id)
        if resume_target is None:
            raise click.ClickException(f"Run {resume_run_id} not found.")
        if not resume_target["resumable"]:
            raise click.ClickException(str(resume_target["resume_error"]))
        stored_task = str(resume_target["run"].get("task_description") or "")
        if task and task != stored_task:
            raise click.ClickException(
                f"Run {resume_run_id} task mismatch. Resume must use the original task description."
            )
        effective_task = stored_task
        checkpoint = resume_target.get("checkpoint")
        step_label = checkpoint["step_index"] if checkpoint is not None else 0
        click.echo(f"resuming_run_id={resume_run_id}")
        click.echo(f"resuming_status={resume_target['run'].get('status')}")
        click.echo(f"resuming_from_step={step_label}")

    agent = make_agent(
        model=model,
        no_memory=no_memory,
        llm_profile=llm_profile,
        trajectory_store=make_trajectory_store(trajectory_dir),
        run_state_store=run_state_store,
    )
    try:
        try:
            result = agent.run(effective_task or "", run_id=effective_run_id, resume=bool(resume_run_id))
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            f"\n[{'OK' if result.success else 'ERR'}] "
            f"steps={result.steps} tools={','.join(result.tool_calls)}"
        )
        if result.extra.get("run_id"):
            key = "resumed_run_id" if resume_run_id else "run_id"
            click.echo(f"{key}={result.extra['run_id']}")
    finally:
        if hasattr(agent, "close"):
            agent.close()
        if run_state_store is not None:
            run_state_store.close()


@cli.group()
def runs() -> None:
    """Inspect persisted agent runs."""


@runs.command(name="list")
@click.option("--limit", default=20, type=int, show_default=True, help="Number of recent runs to show")
def runs_list(limit: int) -> None:
    store = _require_run_state_store()
    try:
        runs_list = store.list_runs(limit=max(1, limit))
        if not runs_list:
            click.echo("No runs found.")
            return
        for run in runs_list:
            click.echo(
                " ".join(
                    [
                        str(run["run_id"])[:8],
                        f"status={run.get('status') or '-'}",
                        f"steps={run.get('total_steps', 0)}",
                        f"termination={run.get('termination_reason') or '-'}",
                        f"created_at={_format_timestamp(run.get('created_at'))}",
                        f"task={_summarize_text(run.get('task_description'))}",
                    ]
                )
            )
    finally:
        store.close()


@runs.command(name="show")
@click.argument("run_id")
def runs_show(run_id: str) -> None:
    store = _require_run_state_store()
    try:
        target = store.get_resume_target(run_id)
        if target is None:
            raise click.ClickException(f"Run {run_id} not found.")
        run = target["run"]
        checkpoint = target["checkpoint"]
        _print_run_detail(run, checkpoint)
        if target["resumable"]:
            click.echo(f"next_command=coder-agent run --resume {run_id}")
        else:
            click.echo(f"resume_status={target['resume_error']}")
    finally:
        store.close()


cli.add_command(chat)
cli.add_command(eval_command)
cli.add_command(memory_command)
cli.add_command(analyze_command)
cli.add_command(serve_command)


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
