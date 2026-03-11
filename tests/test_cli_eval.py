import importlib

from click.testing import CliRunner

from coder_agent.cli.factory import BENCHMARK_CANDIDATE_PRESETS
from coder_agent.cli.main import cli, resolve_agent_config
from coder_agent.eval.runner import TaskSpec


eval_module = importlib.import_module("coder_agent.cli.eval")


def test_resolve_agent_config_uses_same_c4_mapping():
    assert resolve_agent_config("C4") == {
        "correction": True,
        "memory": True,
        "planning_mode": "react",
    }


def test_resolve_agent_config_supports_c6_verification_gate():
    assert resolve_agent_config("C6") == {
        "correction": True,
        "memory": False,
        "planning_mode": "react",
        "verification_gate": True,
    }


def test_eval_rejects_compare_and_preset_together():
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["eval", "--compare", "C1", "--preset", "C4"],
    )

    assert result.exit_code != 0
    assert "--compare and --preset are mutually exclusive" in result.output


def test_release_candidate_presets_are_c3_c4_c6():
    assert BENCHMARK_CANDIDATE_PRESETS == ("C3", "C4", "C6")


def test_eval_rejects_unknown_compare_labels():
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["eval", "--compare", "C3,C9"],
    )

    assert result.exit_code != 0
    assert "Unknown preset label(s): C9" in result.output


def test_eval_filters_custom_tasks_by_task_id(monkeypatch):
    captured = {}

    def fake_load_custom_tasks(tasks_file=None):
        return [
            TaskSpec(task_id="task_a", description="task_a"),
            TaskSpec(task_id="task_b", description="task_b"),
            TaskSpec(task_id="task_c", description="task_c"),
        ]

    def fake_run_suite(self, tasks, **kwargs):
        captured["task_ids"] = [task.task_id for task in tasks]

    monkeypatch.setattr("coder_agent.eval.benchmarks.custom.loader.load_custom_tasks", fake_load_custom_tasks)
    monkeypatch.setattr(eval_module.EvalRunner, "run_suite", fake_run_suite)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["eval", "--benchmark", "custom", "--preset", "C3", "--task-id", "task_b", "--task-id", "task_c"],
    )

    assert result.exit_code == 0
    assert captured["task_ids"] == ["task_b", "task_c"]


def test_eval_filters_compare_tasks_by_task_id(monkeypatch):
    captured = {}

    def fake_load_custom_tasks(tasks_file=None):
        return [
            TaskSpec(task_id="task_a", description="task_a"),
            TaskSpec(task_id="task_b", description="task_b"),
        ]

    def fake_compare_configs(self, tasks, configs, **kwargs):
        captured["task_ids"] = [task.task_id for task in tasks]
        captured["configs"] = sorted(configs)

    monkeypatch.setattr("coder_agent.eval.benchmarks.custom.loader.load_custom_tasks", fake_load_custom_tasks)
    monkeypatch.setattr(eval_module.EvalRunner, "compare_configs", fake_compare_configs)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["eval", "--benchmark", "custom", "--compare", "C3,C4", "--task-id", "task_b"],
    )

    assert result.exit_code == 0
    assert captured["task_ids"] == ["task_b"]
    assert captured["configs"] == ["eval_C3", "eval_C4"]


def test_eval_rejects_unknown_task_ids(monkeypatch):
    def fake_load_custom_tasks(tasks_file=None):
        return [TaskSpec(task_id="task_a", description="task_a")]

    monkeypatch.setattr("coder_agent.eval.benchmarks.custom.loader.load_custom_tasks", fake_load_custom_tasks)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["eval", "--benchmark", "custom", "--task-id", "missing_task"],
    )

    assert result.exit_code != 0
    assert "Unknown task id(s): missing_task" in result.output
