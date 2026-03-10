from io import StringIO

from click.testing import CliRunner
from rich.console import Console

from coder_agent.cli.chat import run_chat_repl
from coder_agent.cli.main import cli
from coder_agent.core import Agent, AgentSession, build_tools
from coder_agent.eval.analysis import TrajectoryAnalyzer
from coder_agent.eval.runner import EvalRunner


class FakeSession:
    def __init__(self):
        self.sent: list[str] = []
        self.reset_calls = 0
        self.meta_turns = 0

    def send(self, text: str):
        self.sent.append(text)
        self.meta_turns += 1
        return type(
            "TurnResult",
            (),
            {
                "success": True,
                "final_status": "success",
                "steps": 1,
                "tool_calls": ["run_command"],
                "termination_reason": "model_stop",
            },
        )()

    def reset(self) -> None:
        self.reset_calls += 1
        self.meta_turns = 0

    def session_metadata(self):
        return type(
            "Meta",
            (),
            {
                "model": "fake-model",
                "workspace": "workspace",
                "memory_enabled": False,
                "turns": self.meta_turns,
            },
        )()


class FakeAgent:
    def __init__(self):
        self.calls: list[str] = []
        self.reset_calls = 0
        self._model_cfg = type("Cfg", (), {"model": "fake-model"})()
        self.memory = None

    def run(self, user_text: str):
        self.calls.append(user_text)
        return type(
            "TurnResult",
            (),
            {
                "content": "ok",
                "steps": 1,
                "tool_calls": [],
                "success": True,
                "retry_steps": 0,
                "total_tokens": 0,
                "trajectory_id": None,
                "final_status": "success",
                "termination_reason": "model_stop",
                "error_details": [],
            },
        )()

    def reset(self) -> None:
        self.reset_calls += 1


def test_public_facades_remain_importable():
    assert Agent is not None
    assert AgentSession is not None
    assert build_tools is not None
    assert EvalRunner is not None
    assert TrajectoryAnalyzer is not None


def test_cli_without_subcommand_enters_chat_and_exits_cleanly():
    runner = CliRunner()
    result = runner.invoke(cli, input="/exit\n")

    assert result.exit_code == 0
    assert "LLM Coding Agent System" in result.output


def test_chat_command_exits_cleanly():
    runner = CliRunner()
    result = runner.invoke(cli, ["chat"], input="/exit\n")

    assert result.exit_code == 0
    assert "Commands: /help /status /reset /clear /exit" in result.output


def test_run_chat_repl_reuses_same_session_and_supports_commands():
    fake_session = FakeSession()
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)

    run_chat_repl(
        fake_session,
        console=console,
        stdin=StringIO("first task\n/status\n/reset\nsecond task\n/exit\n"),
    )

    rendered = output.getvalue()
    assert fake_session.sent == ["first task", "second task"]
    assert fake_session.reset_calls == 1
    assert "model=fake-model" in rendered
    assert "termination=model_stop" in rendered


def test_agent_session_reuses_same_agent_until_reset():
    agent = FakeAgent()
    session = AgentSession(agent)

    session.send("one")
    session.send("two")
    session.reset()
    session.send("three")

    assert agent.calls == ["one", "two", "three"]
    assert agent.reset_calls == 1
    assert session.session_metadata().turns == 1
