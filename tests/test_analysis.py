import json

from coder_agent.eval.analysis import TrajectoryAnalyzer, _is_context_lost


def test_is_context_lost_handles_terminal_steps_without_action():
    trajectory = {
        "steps": [
            {"action": {"tool": "write_file", "args": {"path": "solution.py"}}},
            {"action": None},
        ]
    }

    assert _is_context_lost(trajectory) is False


def test_analyzer_load_deduplicates_resumed_tasks(tmp_path):
    trajectory_path = tmp_path / "exp.jsonl"
    first = {
        "task_id": "HumanEval_1",
        "experiment_id": "exp",
        "config": {},
        "steps": [{"action": None, "is_retry": False}],
        "final_status": "failed",
        "termination_reason": "model_stop",
        "total_tokens": 10,
        "duration": 1.0,
    }
    latest = {
        "task_id": "HumanEval_1",
        "experiment_id": "exp",
        "config": {},
        "steps": [{"action": None, "is_retry": False}, {"action": None, "is_retry": True}],
        "final_status": "success",
        "termination_reason": "model_stop",
        "total_tokens": 20,
        "duration": 2.0,
    }
    second_task = {
        "task_id": "HumanEval_2",
        "experiment_id": "exp",
        "config": {},
        "steps": [{"action": None, "is_retry": False}],
        "final_status": "timeout",
        "termination_reason": "max_steps",
        "total_tokens": 30,
        "duration": 3.0,
    }
    trajectory_path.write_text(
        "\n".join(json.dumps(item) for item in (first, latest, second_task)) + "\n",
        encoding="utf-8",
    )

    analyzer = TrajectoryAnalyzer(trajectory_dir=tmp_path)
    loaded = analyzer._load("exp")
    stats = analyzer.compute_statistics("exp")

    assert len(loaded) == 2
    assert loaded[0]["final_status"] == "success"
    assert stats.total_trajectories == 2
    assert stats.success_count == 1
    assert stats.timeout_count == 1
    assert stats.termination_reasons == {"model_stop": 1, "max_steps": 1}
