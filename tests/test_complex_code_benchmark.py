import pytest
from pathlib import Path
import shutil
import subprocess
import sys
from coder_agent.eval.complex_models import ComplexCodeTaskSpec
from coder_agent.eval.benchmarks.complex_code.loader import load_complex_code_tasks
from coder_agent.eval.benchmarks.complex_code.replay import audit_clean_replays, replay_task
from coder_agent.eval.eval_workspace import prepare_workspace


def test_complex_task_requires_executable_checks_and_profile_rationale():
    raw = {"task_id": "complex_001", "description": "migrate safely", "setup_files": ["app.py"], "verification": [{"cmd": "python -m pytest"}], "complexity_profile": {"dimensions": {"migration": 3, "compatibility": 2}, "rationale": "Requires compatible state migration."}}
    assert ComplexCodeTaskSpec.from_dict(raw).complexity_profile.dimensions["migration"] == 3
    raw["complexity_profile"]["dimensions"] = {"unknown": 1}
    with pytest.raises(ValueError, match="known dimensions"):
        ComplexCodeTaskSpec.from_dict(raw)


def test_checked_in_complex_fixture_has_buggy_fail_and_gold_pass(tmp_path):
    root = Path(__file__).parents[1] / "coder_agent/eval/benchmarks/complex_code"
    tasks = load_complex_code_tasks(root / "tasks.yaml")
    assert tasks[0].task_id == "complex_v1_migration_idempotency"
    replay = replay_task(tasks[0], root, tmp_path / "workspace")
    assert replay.buggy_failed and replay.gold_passed
    audit = audit_clean_replays(tasks, root, tmp_path / "audit", repeats=3)
    assert len(audit[tasks[0].task_id]) == 3


def test_fixture_root_rejects_path_escape(tmp_path):
    fixtures = tmp_path / "fixtures"; fixtures.mkdir(); (fixtures / "safe.txt").write_text("safe")
    with pytest.raises(ValueError, match="escapes"):
        prepare_workspace(["../outside.txt"], tmp_path / "workspace", setup_dir=fixtures)
