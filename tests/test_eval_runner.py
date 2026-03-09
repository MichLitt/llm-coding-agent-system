from types import MethodType

from coder_agent.eval.metrics import EvalResult
from coder_agent.eval.runner import EvalRunner, TaskSpec


class DummyAgent:
    def __init__(self, experiment_config=None):
        self.experiment_config = experiment_config or {}
        self.run_calls = []

    def reset(self) -> None:
        return None

    def run(self, description, task_id="", finalize_trajectory=True, verification_hook=None, max_verification_attempts=2):
        self.run_calls.append({
            "description": description,
            "task_id": task_id,
            "verification_hook": verification_hook,
            "max_verification_attempts": max_verification_attempts,
        })
        return type("TurnResult", (), {
            "final_status": "success",
            "steps": 1,
            "retry_steps": 0,
            "termination_reason": "model_stop",
            "total_tokens": 10,
            "trajectory_id": None,
            "error_details": [],
        })()


def _result(task_id: str, config_label: str = "eval") -> EvalResult:
    return EvalResult(
        task_id=task_id,
        success=True,
        benchmark_passed=True,
        agent_completed_cleanly=True,
        agent_final_status="success",
        checks_passed=1,
        checks_total=1,
        steps_used=1,
        retry_steps=0,
        termination_reason="model_stop",
        verification_pass_rate=1.0,
        total_tokens=10,
        duration=0.1,
        config_label=config_label,
    )


def test_run_suite_writes_checkpoint_and_resume_skips_completed(tmp_path):
    tasks = [TaskSpec(task_id=task_id, description=task_id) for task_id in ("task_a", "task_b", "task_c")]
    runner = EvalRunner(agent_factory=lambda _: DummyAgent(), output_dir=tmp_path)

    calls: list[str] = []

    def interrupted_run_task(self, task, agent, config_label=""):
        calls.append(task.task_id)
        if task.task_id == "task_b":
            raise KeyboardInterrupt("stop after checkpoint")
        return _result(task.task_id, config_label)

    runner.run_task = MethodType(interrupted_run_task, runner)

    try:
        runner.run_suite(
            tasks,
            config_label="resume_demo",
            benchmark_name="custom",
            preset="C4",
            resume=False,
            verbose=False,
        )
    except KeyboardInterrupt:
        pass

    checkpoint_lines = (tmp_path / "resume_demo.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(checkpoint_lines) == 1
    manifest = (tmp_path / "resume_demo_run_manifest.json").read_text(encoding="utf-8")
    assert '"completed_task_ids": [' in manifest
    assert "task_a" in manifest

    runner_resume = EvalRunner(agent_factory=lambda _: DummyAgent(), output_dir=tmp_path)
    resumed_calls: list[str] = []

    def resumed_run_task(self, task, agent, config_label=""):
        resumed_calls.append(task.task_id)
        return _result(task.task_id, config_label)

    runner_resume.run_task = MethodType(resumed_run_task, runner_resume)
    results = runner_resume.run_suite(
        tasks,
        config_label="resume_demo",
        benchmark_name="custom",
        preset="C4",
        resume=True,
        verbose=False,
    )

    assert [result.task_id for result in results] == ["task_a", "task_b", "task_c"]
    assert resumed_calls == ["task_b", "task_c"]


def test_run_suite_without_resume_clears_old_checkpoint(tmp_path):
    tasks = [TaskSpec(task_id="fresh_task", description="fresh")]
    runner = EvalRunner(agent_factory=lambda _: DummyAgent(), output_dir=tmp_path)

    (tmp_path / "fresh.jsonl").write_text(
        '{"task_id":"stale","success":true,"benchmark_passed":true,'
        '"agent_completed_cleanly":true,"agent_final_status":"success",'
        '"checks_passed":1,"checks_total":1,"steps_used":1,"retry_steps":0,'
        '"termination_reason":"model_stop","verification_pass_rate":1.0,'
        '"total_tokens":1,"duration":0.1,"error_types":[],"config_label":"fresh"}\n',
        encoding="utf-8",
    )
    (tmp_path / "fresh.json").write_text("[]", encoding="utf-8")
    (tmp_path / "fresh_run_manifest.json").write_text("{}", encoding="utf-8")

    def fake_run_task(self, task, agent, config_label=""):
        return _result(task.task_id, config_label)

    runner.run_task = MethodType(fake_run_task, runner)
    results = runner.run_suite(
        tasks,
        config_label="fresh",
        benchmark_name="custom",
        preset="default",
        resume=False,
        verbose=False,
    )

    assert [result.task_id for result in results] == ["fresh_task"]
    checkpoint_content = (tmp_path / "fresh.jsonl").read_text(encoding="utf-8")
    assert "stale" not in checkpoint_content
    assert "fresh_task" in checkpoint_content


def test_run_task_passes_verification_hook_only_when_gate_enabled(tmp_path):
    task = TaskSpec(
        task_id="custom_task",
        description="fix task",
        verification=[{"cmd": "python -c \"print('ok')\""}],
    )

    gate_agent = DummyAgent({"verification_gate": True})
    runner = EvalRunner(agent_factory=lambda _: gate_agent, output_dir=tmp_path)
    result = runner.run_task(task, gate_agent, config_label="c6")

    assert result.success is True
    assert gate_agent.run_calls[0]["verification_hook"] is not None
    assert gate_agent.run_calls[0]["max_verification_attempts"] == 2

    no_gate_agent = DummyAgent({"verification_gate": False})
    runner.run_task(task, no_gate_agent, config_label="c3")
    assert no_gate_agent.run_calls[0]["verification_hook"] is None
