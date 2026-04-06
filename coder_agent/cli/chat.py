import sys
from typing import TextIO

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console

from coder_agent.core.session import AgentSession


def _print_banner(console: Console) -> None:
    console.print("[bold]LLM Coding Agent System[/bold]")
    console.print("Commands: /help /status /reset /clear /exit\n")


def _print_status(console: Console, agent_session: AgentSession) -> None:
    meta = agent_session.session_metadata()
    console.print(
        f"[cyan]model[/cyan]={meta.model} "
        f"[cyan]memory[/cyan]={'on' if meta.memory_enabled else 'off'} "
        f"[cyan]workspace[/cyan]={meta.workspace} "
        f"[cyan]turns[/cyan]={meta.turns}"
    )


def _print_summary(console: Console, result) -> None:
    tools = ",".join(result.tool_calls) if result.tool_calls else "-"
    console.print(
        f"\n[dim]success={result.success} final={result.final_status} "
        f"steps={result.steps} tools={tools} termination={result.termination_reason}[/dim]\n"
    )


def _handle_command(console: Console, agent_session: AgentSession, text: str) -> bool:
    command = text.strip().lower()
    if command in {"exit", "quit", "/exit"}:
        return False
    if command == "/help":
        console.print("Commands: /help /status /reset /clear /exit")
        return True
    if command == "/status":
        _print_status(console, agent_session)
        return True
    if command == "/reset":
        agent_session.reset()
        console.print("[green]Session reset.[/green]")
        return True
    if command == "/clear":
        console.clear()
        _print_banner(console)
        return True
    return None


def run_chat_repl(
    agent_session: AgentSession,
    *,
    console: Console | None = None,
    stdin: TextIO | None = None,
) -> None:
    console = console or Console()
    stdin = stdin or click.get_text_stream("stdin")
    _print_banner(console)

    interactive = hasattr(stdin, "isatty") and stdin.isatty()
    prompt = PromptSession("> ", history=InMemoryHistory()) if interactive else None

    try:
        while True:
            try:
                if prompt is not None:
                    text = prompt.prompt().strip()
                else:
                    line = stdin.readline()
                    if not line:
                        break
                    text = line.strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            if not text:
                continue

            command_result = _handle_command(console, agent_session, text)
            if command_result is False:
                break
            if command_result is True:
                continue

            try:
                result = agent_session.send(text)
            except Exception as exc:
                console.print(f"[red]Agent error:[/red] {exc}")
                continue
            _print_summary(console, result)
    finally:
        if hasattr(agent_session, "close"):
            agent_session.close()


@click.command()
@click.option("--model", default=None, help="Override model name from config.yaml")
@click.option("--no-memory", is_flag=True, help="Disable long-term memory")
@click.option("--llm-profile", default=None, help="Named LLM profile from config.yaml llm.profiles")
def chat(model: str | None, no_memory: bool, llm_profile: str | None) -> None:
    from coder_agent.cli.factory import make_session

    run_chat_repl(make_session(model=model, no_memory=no_memory, llm_profile=llm_profile), stdin=sys.stdin)
