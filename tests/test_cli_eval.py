import importlib
from pathlib import Path

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


def test_eval_loads_swebench_subset(monkeypatch):
    captured = {}

    def fake_load_swebench_tasks(subset="smoke"):
        captured["subset"] = subset
        return [TaskSpec(task_id="swe_task", description="demo", metadata={"benchmark": "swebench"})]

    def fake_run_suite(self, tasks, **kwargs):
        captured["task_ids"] = [task.task_id for task in tasks]
        captured["benchmark_name"] = kwargs["benchmark_name"]

    monkeypatch.setattr("coder_agent.eval.benchmarks.swebench.loader.load_swebench_tasks", fake_load_swebench_tasks)
    monkeypatch.setattr(eval_module.EvalRunner, "run_suite", fake_run_suite)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["eval", "--benchmark", "swebench", "--swebench-subset", "promoted", "--preset", "C3"],
    )

    assert result.exit_code == 0
    assert captured["subset"] == "promoted"
    assert captured["task_ids"] == ["swe_task"]
    assert captured["benchmark_name"] == "swebench"


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


def test_eval_rejects_invalid_experiment_config_json():
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["eval", "--experiment-config", "{bad json}"],
    )

    assert result.exit_code != 0
    assert "Invalid JSON for --experiment-config" in result.output


def test_eval_single_run_passes_config_label_and_experiment_config_to_make_agent(monkeypatch):
    captured = {}

    def fake_load_custom_tasks(tasks_file=None):
        return [TaskSpec(task_id="task_a", description="task_a")]

    def fake_make_agent(agent_cfg=None, **kwargs):
        captured["agent_cfg"] = agent_cfg
        captured["kwargs"] = kwargs
        return object()

    def fake_run_suite(self, tasks, **kwargs):
        captured["task_ids"] = [task.task_id for task in tasks]
        self.agent_factory(kwargs["agent_config"], Path("/tmp/demo-workspace"))
        captured["run_suite_kwargs"] = kwargs

    monkeypatch.setattr("coder_agent.eval.benchmarks.custom.loader.load_custom_tasks", fake_load_custom_tasks)
    monkeypatch.setattr(eval_module, "make_agent", fake_make_agent)
    monkeypatch.setattr(eval_module.EvalRunner, "run_suite", fake_run_suite)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "eval",
            "--benchmark",
            "custom",
            "--preset",
            "C4",
            "--config-label",
            "demo_run",
            "--experiment-config",
            '{"memory_lookup_mode":"similarity","keep_recent_turns":4}',
        ],
    )

    assert result.exit_code == 0
    assert captured["task_ids"] == ["task_a"]
    assert captured["kwargs"]["config_label"] == "demo_run"
    assert captured["kwargs"]["workspace"] == Path("/tmp/demo-workspace")
    assert captured["kwargs"]["experiment_config"] == {
        "memory_lookup_mode": "similarity",
        "keep_recent_turns": 4,
    }
    assert captured["run_suite_kwargs"]["experiment_config"] == {
        "memory_lookup_mode": "similarity",
        "keep_recent_turns": 4,
    }


def test_eval_compare_uses_per_config_labels_for_make_agent(monkeypatch):
    captured = []

    def fake_load_custom_tasks(tasks_file=None):
        return [TaskSpec(task_id="task_a", description="task_a")]

    def fake_make_agent(agent_cfg=None, **kwargs):
        captured.append((agent_cfg, kwargs))
        return object()

    def fake_compare_configs(self, tasks, configs, **kwargs):
        for config in configs.values():
            self.agent_factory(config, Path("/tmp/demo-workspace"))
        return None

    monkeypatch.setattr("coder_agent.eval.benchmarks.custom.loader.load_custom_tasks", fake_load_custom_tasks)
    monkeypatch.setattr(eval_module, "make_agent", fake_make_agent)
    monkeypatch.setattr(eval_module.EvalRunner, "compare_configs", fake_compare_configs)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "eval",
            "--benchmark",
            "custom",
            "--compare",
            "C3,C4",
            "--config-label",
            "batch",
            "--experiment-config",
            '{"memory_lookup_mode":"similarity"}',
        ],
    )

    assert result.exit_code == 0
    labels = [kwargs["config_label"] for _, kwargs in captured]
    assert labels == ["batch_C3", "batch_C4"]
    assert all(kwargs["workspace"] == Path("/tmp/demo-workspace") for _, kwargs in captured)
    assert all(kwargs["experiment_config"] == {"memory_lookup_mode": "similarity"} for _, kwargs in captured)
