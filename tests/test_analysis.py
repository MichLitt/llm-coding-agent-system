import json

from coder_agent.eval.analysis import TrajectoryAnalyzer, _is_context_lost
from coder_agent.eval.analysis_taxonomy import classify_layered_failure


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


def test_layered_failure_report_writes_expected_schema(tmp_path):
    trajectory_path = tmp_path / "exp.jsonl"
    failed = {
        "task_id": "swe_task",
        "experiment_id": "exp",
        "metadata": {
            "expected_patch_targets": ["app/service.py"],
            "authorized_test_edit_paths": [],
            "verification_files": [],
        },
        "steps": [
            {
                "action": {"tool": "write_file", "args": {"path": "tests/test_service.py"}},
                "observation": "Verification recovery blocked an unlisted test edit.",
                "is_retry": True,
            }
        ],
        "final_status": "failed",
        "termination_reason": "retry_exhausted",
        "total_tokens": 10,
        "duration": 1.0,
    }
    trajectory_path.write_text(json.dumps(failed) + "\n", encoding="utf-8")

    analyzer = TrajectoryAnalyzer(trajectory_dir=tmp_path)
    report = analyzer.layered_failure_report("exp")
    output_path = analyzer.write_analysis_report("exp", output_dir=tmp_path)

    assert report["summary"]["total_failed"] == 1
    assert report["summary"]["layered_failure_counts"]["test_drift"] == 1
    assert report["per_task"][0]["primary_category"] == "test_drift"
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["experiment_id"] == "exp"
    assert saved["per_task"][0]["task_id"] == "swe_task"


def test_trajectory_store_persists_task_metadata(tmp_path):
    from coder_agent.memory.trajectory import Step, TrajectoryStore

    store = TrajectoryStore(tmp_path)
    traj_id = store.start_trajectory(
        task_id="task_a",
        experiment_id="exp",
        config={"correction": True},
        task_metadata={"expected_patch_targets": ["app/service.py"]},
    )
    store.record_step(
        traj_id,
        Step(
            step_id=1,
            thought="fix",
            action={"tool": "patch_file", "args": {"path": "app/service.py"}},
            observation="ok",
            timestamp=0.0,
        ),
    )
    store.finish_trajectory(traj_id, final_status="failed", termination_reason="retry_exhausted")

    loaded = store.load("exp")
    assert loaded[0]["task_metadata"]["expected_patch_targets"] == ["app/service.py"]


def test_classify_layered_failure_detects_verification_overlay_conflict():
    trajectory = {
        "task_id": "flask_task",
        "task_metadata": {
            "authorized_test_edit_paths": ["tests/static/config.toml"],
            "verification_files": ["tests/static/config.toml"],
        },
        "steps": [
            {
                "action": None,
                "observation": (
                    "SWE-bench verification failed. verification test_patch apply failed: "
                    "error: tests/static/config.toml: already exists in working directory"
                ),
                "error_type": "VerificationFailed",
            }
        ],
        "final_status": "failed",
    }

    primary, secondary, notes = classify_layered_failure(trajectory)

    assert primary == "verification_overlay_conflict"
    assert secondary == []
    assert "official regression overlay" in notes


def test_classify_layered_failure_detects_shell_exit_masking():
    trajectory = {
        "task_id": "pytest_task",
        "task_metadata": {},
        "steps": [
            {
                "action": {
                    "tool": "run_command",
                    "args": {
                        "command": "python -m pytest -q testing/test_mark.py 2>&1 | tail -10",
                    },
                },
                "observation": "Exit code: 0\nSTDOUT:\n166 passed, 1 xfailed in 1.81s\n",
            },
            {
                "action": None,
                "observation": "SWE-bench verification failed. pass_to_pass regressed: testing/test_mark.py::test_mark_option[not",
                "error_type": "VerificationFailed",
            },
        ],
        "final_status": "failed",
    }

    primary, secondary, notes = classify_layered_failure(trajectory)

    assert primary == "shell_exit_masking"
    assert secondary == []
    assert "pipe" in notes.lower()


def test_classify_layered_failure_does_not_keep_shell_exit_masking_after_direct_rerun():
    trajectory = {
        "task_id": "pytest_task",
        "task_metadata": {},
        "steps": [
            {
                "action": {
                    "tool": "run_command",
                    "args": {
                        "command": "python -m pytest -q testing/test_mark.py 2>&1 | tail -10",
                    },
                },
                "observation": "Exit code: 0\nSTDOUT:\n166 passed, 1 xfailed in 1.81s\n",
            },
            {
                "action": {
                    "tool": "run_command",
                    "args": {
                        "command": "python -m pytest -q testing/test_mark.py testing/test_skipping.py",
                    },
                },
                "observation": "Exit code: 0\nSTDOUT:\n166 passed, 1 xfailed in 1.81s\n",
            },
            {
                "action": None,
                "observation": "SWE-bench verification failed. pass_to_pass regressed: testing/test_mark.py::test_mark_option[not",
                "error_type": "VerificationFailed",
            },
        ],
        "final_status": "failed",
    }

    primary, secondary, notes = classify_layered_failure(trajectory)

    assert primary == "genuine_implementation_miss"
    assert secondary == []
    assert "implementation miss" in notes
