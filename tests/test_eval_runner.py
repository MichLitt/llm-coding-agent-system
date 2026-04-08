import json
from types import MethodType

import pytest

from coder_agent.config import cfg
from coder_agent.eval.metrics import EvalResult
from coder_agent.eval.runner import EvalRunner, TaskSpec


class DummyAgent:
    def __init__(self, experiment_config=None):
        self.experiment_config = experiment_config or {}
        self.run_calls = []
        self.close_calls = 0

    def reset(self) -> None:
        return None

    def close(self) -> None:
        self.close_calls += 1

    def run(
        self,
        description,
        task_id="",
        finalize_trajectory=True,
        verification_hook=None,
        max_verification_attempts=2,
        enforce_stop_verification=True,
        auto_complete_on_verification=False,
        max_steps=None,
    ):
        self.run_calls.append({
            "description": description,
            "task_id": task_id,
            "verification_hook": verification_hook,
            "max_verification_attempts": max_verification_attempts,
            "enforce_stop_verification": enforce_stop_verification,
            "auto_complete_on_verification": auto_complete_on_verification,
            "max_steps": max_steps,
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
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)

    calls: list[str] = []

    def interrupted_run_task(self, task, agent, config_label="", workspace=None, run_id=None):
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

    runner_resume = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)
    resumed_calls: list[str] = []

    def resumed_run_task(self, task, agent, config_label="", workspace=None, run_id=None):
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
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)

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

    def fake_run_task(self, task, agent, config_label="", workspace=None, run_id=None):
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


def test_run_task_routes_stop_gate_and_auto_complete_independently(tmp_path):
    task = TaskSpec(
        task_id="custom_task",
        description="fix task",
        verification=[{"cmd": "python -c \"print('ok')\""}],
    )

    gate_agent = DummyAgent({"verification_gate": True})
    runner = EvalRunner(agent_factory=lambda _, __: gate_agent, output_dir=tmp_path)
    result = runner.run_task(task, gate_agent, config_label="c6")

    assert result.success is True
    assert gate_agent.run_calls[0]["verification_hook"] is not None
    assert gate_agent.run_calls[0]["max_verification_attempts"] == 2
    assert gate_agent.run_calls[0]["enforce_stop_verification"] is True
    assert gate_agent.run_calls[0]["auto_complete_on_verification"] is True

    # After Fix 1 (v0.4.4): enforce_stop_verification is always True when a
    # verification hook exists, regardless of the experiment_config verification_gate flag.
    no_gate_agent = DummyAgent({"verification_gate": False})
    runner.run_task(task, no_gate_agent, config_label="c3")
    assert no_gate_agent.run_calls[0]["verification_hook"] is not None
    assert no_gate_agent.run_calls[0]["enforce_stop_verification"] is True
    assert no_gate_agent.run_calls[0]["auto_complete_on_verification"] is True


def test_run_task_passes_task_level_max_steps(tmp_path):
    task = TaskSpec(task_id="custom_task", description="fix task", max_steps=7)
    agent = DummyAgent()
    runner = EvalRunner(agent_factory=lambda _, __: agent, output_dir=tmp_path)

    runner.run_task(task, agent, config_label="c6")

    assert agent.run_calls[0]["max_steps"] == 7


def test_run_suite_closes_agent_on_success(tmp_path):
    agent = DummyAgent()
    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    runner = EvalRunner(agent_factory=lambda _, __: agent, output_dir=tmp_path)

    def fake_run_task(self, task, current_agent, config_label="", workspace=None, run_id=None):
        assert current_agent is agent
        return _result(task.task_id, config_label)

    runner.run_task = MethodType(fake_run_task, runner)
    runner.run_suite(
        tasks,
        config_label="close_demo",
        benchmark_name="custom",
        preset="C4",
        resume=False,
        verbose=False,
    )

    assert agent.close_calls == 1


def test_run_suite_closes_agent_on_exception(tmp_path):
    agent = DummyAgent()
    tasks = [TaskSpec(task_id=task_id, description=task_id) for task_id in ("task_a", "task_b")]
    runner = EvalRunner(agent_factory=lambda _, __: agent, output_dir=tmp_path)

    def interrupted_run_task(self, task, current_agent, config_label="", workspace=None, run_id=None):
        assert current_agent is agent
        if task.task_id == "task_b":
            raise KeyboardInterrupt("stop")
        return _result(task.task_id, config_label)

    runner.run_task = MethodType(interrupted_run_task, runner)

    with pytest.raises(KeyboardInterrupt):
        runner.run_suite(
            tasks,
            config_label="close_demo_interrupt",
            benchmark_name="custom",
            preset="C4",
            resume=False,
            verbose=False,
        )

    assert agent.close_calls == 1


def test_run_suite_manifest_includes_git_and_config_fingerprints(tmp_path, monkeypatch):
    from coder_agent.eval import eval_checkpoint

    monkeypatch.setattr(
        eval_checkpoint,
        "_git_snapshot",
        lambda: {
            "git_commit": "abc1234",
            "git_commit_short": "abc1234",
            "git_commit_full": "abc1234567890",
            "git_is_dirty": True,
            "git_status_porcelain": " M coder_agent/core/context.py",
            "git_diff_tracked_sha256": "diffhash",
            "git_untracked_files": ["scratch.txt"],
        },
    )

    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)
    runner.run_suite(
        tasks,
        config_label="manifest_demo",
        agent_config={"history_compaction_mode": "semantic"},
        experiment_config={"memory_lookup_mode": "similarity"},
        benchmark_name="custom",
        preset="ctx3",
        resume=False,
        verbose=False,
    )

    manifest = (tmp_path / "manifest_demo_run_manifest.json").read_text(encoding="utf-8")
    assert '"git_commit_full": "abc1234567890"' in manifest
    assert '"git_is_dirty": true' in manifest
    assert '"git_diff_tracked_sha256": "diffhash"' in manifest
    assert '"agent_config_sha256":' in manifest
    assert '"runtime_experiment_config_sha256":' in manifest
    assert '"experiment_config_sha256":' in manifest
    assert '"history_compaction_mode": "semantic"' in manifest
    assert '"memory_lookup_mode": "similarity"' in manifest
    assert '"run_id":' in manifest
    assert '"workspace_mode": "per_run_v1"' in manifest
    assert '"workspace_path":' in manifest
    assert '"task_ids": [' in manifest


def test_run_suite_passes_run_workspace_to_agent_factory(tmp_path):
    captured_workspaces = []
    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    runner = EvalRunner(
        agent_factory=lambda _, workspace: captured_workspaces.append(workspace) or DummyAgent(),
        output_dir=tmp_path,
    )

    runner._allocate_run_id = lambda: "20260407120000-aaaabbbb"
    runner.run_suite(tasks, config_label="c4_lane", benchmark_name="custom", preset="C4", verbose=False)

    assert captured_workspaces == [cfg.agent.workspace.resolve() / "c4_lane" / "20260407120000-aaaabbbb"]


def test_run_suite_allocates_distinct_run_workspaces_for_non_resume_runs(tmp_path):
    captured_workspaces = []
    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    runner = EvalRunner(
        agent_factory=lambda _, workspace: captured_workspaces.append(workspace) or DummyAgent(),
        output_dir=tmp_path,
    )
    run_ids = iter(["20260407120000-aaaabbbb", "20260407120000-ccccdddd"])
    runner._allocate_run_id = lambda: next(run_ids)

    runner.run_suite(tasks, config_label="repeat_demo", benchmark_name="custom", preset="C4", verbose=False)
    runner.run_suite(tasks, config_label="repeat_demo", benchmark_name="custom", preset="C4", verbose=False)

    assert len(captured_workspaces) == 2
    assert captured_workspaces[0] != captured_workspaces[1]


def test_run_task_uses_explicit_workspace_for_prepare_and_checks(tmp_path, monkeypatch):
    task = TaskSpec(task_id="custom_task", description="fix task", verification=[{"cmd": "python -m pytest"}])
    agent = DummyAgent()
    runner = EvalRunner(agent_factory=lambda _, __: agent, output_dir=tmp_path)
    explicit_workspace = tmp_path / "runs" / "demo"
    captured = {}

    monkeypatch.setattr(
        "coder_agent.eval.runner.prepare_workspace",
        lambda setup_files, workspace: captured.setdefault("prepare", workspace),
    )
    monkeypatch.setattr(
        "coder_agent.eval.runner.build_verification_hook",
        lambda task_spec, workspace: captured.setdefault("hook", workspace) or None,
    )
    def fake_run_custom_checks(checks, workspace):
        captured["checks"] = workspace
        return 1

    monkeypatch.setattr(runner, "_run_custom_checks", fake_run_custom_checks)

    runner.run_task(task, agent, config_label="demo", workspace=explicit_workspace, run_id="run-1")

    assert captured["prepare"] == explicit_workspace
    assert captured["hook"] == explicit_workspace
    assert captured["checks"] == explicit_workspace


def test_run_suite_resume_legacy_manifest_fails(tmp_path):
    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)
    (tmp_path / "legacy_run_manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="legacy eval run"):
        runner.run_suite(
            tasks,
            config_label="legacy",
            benchmark_name="custom",
            preset="C4",
            resume=True,
            verbose=False,
        )


def test_run_suite_resume_rejects_hash_mismatch(tmp_path):
    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)
    runner._allocate_run_id = lambda: "20260407120000-aaaabbbb"
    runner.run_suite(
        tasks,
        config_label="hash_demo",
        benchmark_name="custom",
        preset="C4",
        experiment_config={"memory_lookup_mode": "similarity"},
        resume=False,
        verbose=False,
    )

    with pytest.raises(ValueError, match="runtime_experiment_config_sha256 mismatch"):
        runner.run_suite(
            tasks,
            config_label="hash_demo",
            benchmark_name="custom",
            preset="C4",
            experiment_config={"memory_lookup_mode": "recent"},
            resume=True,
            verbose=False,
        )


def test_run_suite_resume_rejects_llm_model_mismatch(tmp_path):
    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)
    runner._allocate_run_id = lambda: "20260407120000-aaaabbbb"
    runner.run_suite(tasks, config_label="llm_demo", benchmark_name="custom", preset="C4", verbose=False)

    manifest_path = tmp_path / "llm_demo_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["llm_model"] = "different-model"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="llm_model mismatch"):
        runner.run_suite(tasks, config_label="llm_demo", benchmark_name="custom", preset="C4", resume=True, verbose=False)


def test_run_suite_resume_rejects_llm_transport_mismatch(tmp_path):
    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)
    runner._allocate_run_id = lambda: "20260407120000-aaaabbbb"
    runner.run_suite(tasks, config_label="transport_demo", benchmark_name="custom", preset="C4", verbose=False)

    manifest_path = tmp_path / "transport_demo_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["llm_transport"] = "different-transport"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="llm_transport mismatch"):
        runner.run_suite(
            tasks,
            config_label="transport_demo",
            benchmark_name="custom",
            preset="C4",
            resume=True,
            verbose=False,
        )


def test_run_suite_resume_warns_on_workspace_path_mismatch(tmp_path):
    tasks = [TaskSpec(task_id="task_a", description="task_a")]
    captured_workspaces = []
    runner = EvalRunner(
        agent_factory=lambda _, workspace: captured_workspaces.append(workspace) or DummyAgent(),
        output_dir=tmp_path,
    )
    runner._allocate_run_id = lambda: "20260407120000-aaaabbbb"
    runner.run_suite(tasks, config_label="warn_demo", benchmark_name="custom", preset="C4", verbose=False)

    manifest_path = tmp_path / "warn_demo_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["workspace_path"] = str(tmp_path / "unexpected" / "workspace")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.warns(UserWarning, match="workspace_path does not match"):
        runner.run_suite(tasks, config_label="warn_demo", benchmark_name="custom", preset="C4", resume=True, verbose=False)

    assert captured_workspaces[-1] == cfg.agent.workspace.resolve() / "warn_demo" / "20260407120000-aaaabbbb"
