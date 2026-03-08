from click.testing import CliRunner

from coder_agent.cli.main import cli, resolve_agent_config


def test_resolve_agent_config_uses_same_c4_mapping():
    assert resolve_agent_config("C4") == {
        "correction": True,
        "memory": True,
        "planning_mode": "react",
    }


def test_eval_rejects_compare_and_preset_together():
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["eval", "--compare", "C1", "--preset", "C4"],
    )

    assert result.exit_code != 0
    assert "--compare and --preset are mutually exclusive" in result.output
