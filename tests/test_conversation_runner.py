from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from coder_agent.eval.conversation_models import ConversationTaskSpec
from coder_agent.eval.conversation_runner import ConversationRunner
from coder_agent.eval.conversation_metrics import compute_conversation_metrics
from coder_agent.eval.benchmarks.conversation.loader import load_conversation_tasks
from coder_agent.eval.benchmarks.conversation.freeze import build_freeze_manifest, validate_freeze_manifest


def _task() -> ConversationTaskSpec:
    return ConversationTaskSpec.from_dict({
        "conversation_id": "conv_dev_001", "split": "dev", "category": "incremental-requirement", "difficulty": "medium",
        "setup_files": ["app.txt"], "max_total_steps": 10,
        "verification_contract": {"constraint_checks": {"preserve_base": [{"cmd": "test \"$(cat app.txt)\" = base"}]}},
        "turns": [
            {"turn_id": "t1", "user_message": "base", "intent": "create", "introduces_constraints": ["preserve_base"], "phase_checks": [{"cmd": "test -f app.txt"}]},
            {"turn_id": "t2", "user_message": "base", "intent": "retain", "retains_constraints": ["preserve_base"]},
            {"turn_id": "t3", "user_message": "base", "intent": "final", "retains_constraints": ["preserve_base"]},
        ], "final_checks": [{"cmd": "test \"$(cat app.txt)\" = base"}],
    })


class _Session:
    def __init__(self, workspace, forget=False, explode=False): self.workspace, self.forget, self.explode = workspace, forget, explode
    def send(self, message, **_kwargs):
        if self.explode: raise RuntimeError("fixture failure")
        if self.forget: self.workspace.joinpath("app.txt").write_text("broken")
        return SimpleNamespace(steps=1, tool_calls=["write_file"], total_tokens=2, final_status="success", termination_reason=None, error_details=[], content="Which format should I use?")
    def close(self): pass


def test_runner_reuses_one_session_and_writes_versioned_artifacts(tmp_path):
    fixtures = tmp_path / "fixtures"; fixtures.mkdir(); (fixtures / "app.txt").write_text("base")
    runner = ConversationRunner(lambda workspace: _Session(workspace), tmp_path / "out", fixtures, run_metadata={"llm_model": "fixture-model"})
    result = runner.run_task(_task(), tmp_path / "workspace")
    assert result.success is True
    assert len(result.turns) == 3
    lines = (tmp_path / "out" / "conversation_turns.jsonl").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["schema_version"] == "conversation-eval/v1"
    suite = runner.run_suite([_task()], tmp_path / "suite")
    manifest = json.loads((tmp_path / "out" / "conversation_run_manifest.json").read_text())
    assert suite[0].success and manifest["fixture_tree_sha256"] and manifest["run_metadata"]["llm_model"] == "fixture-model"


def test_runner_does_not_mark_constraint_forgetting_or_exception_completed(tmp_path):
    fixtures = tmp_path / "fixtures"; fixtures.mkdir(); (fixtures / "app.txt").write_text("base")
    task = _task()
    forgotten = ConversationRunner(lambda workspace: _Session(workspace, forget=True), tmp_path / "forgot", fixtures).run_task(task, tmp_path / "w1")
    exploded = ConversationRunner(lambda workspace: _Session(workspace, explode=True), tmp_path / "explode", fixtures).run_task(task, tmp_path / "w2")
    assert forgotten.success is False and "constraint_failure" in forgotten.failure_categories
    assert exploded.success is False and "infra_exception:RuntimeError" in exploded.failure_categories


def test_schema_rejects_unmapped_constraint():
    raw = {"conversation_id": "x", "split": "dev", "category": "x", "difficulty": "x", "turns": [{"turn_id": str(i), "user_message": "x", "intent": "x", "introduces_constraints": ["missing"]} for i in range(3)], "final_checks": [], "verification_contract": {"constraint_checks": {}}, "setup_files": [], "max_total_steps": 3}
    with pytest.raises(ValueError, match="constraint"):
        ConversationTaskSpec.from_dict(raw)


def test_resume_skips_only_successfully_completed_conversation_boundaries(tmp_path):
    fixtures = tmp_path / "fixtures"; fixtures.mkdir(); (fixtures / "app.txt").write_text("base")
    calls = []
    def factory(workspace):
        calls.append(workspace)
        return _Session(workspace)
    runner = ConversationRunner(factory, tmp_path / "out", fixtures)
    runner.run_suite([_task()], tmp_path / "work")
    runner.run_suite([_task()], tmp_path / "work", resume=True)
    assert len(calls) == 1


def test_resume_recovers_completed_task_after_interruption(tmp_path):
    fixtures = tmp_path / "fixtures"; fixtures.mkdir(); (fixtures / "app.txt").write_text("base")
    first = _task()
    second_raw = first.snapshot(); second_raw["conversation_id"] = "conv_dev_002"
    second = ConversationTaskSpec.from_dict(second_raw)
    calls = []

    def factory(workspace):
        calls.append(workspace)
        return _Session(workspace)

    runner = ConversationRunner(factory, tmp_path / "out", fixtures)
    original_run_task = runner.run_task

    def interrupt_on_second(task, workspace):
        if task.conversation_id == second.conversation_id:
            raise RuntimeError("simulated interruption")
        return original_run_task(task, workspace)

    runner.run_task = interrupt_on_second  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="simulated interruption"):
        runner.run_suite([first, second], tmp_path / "work")

    manifest = json.loads((tmp_path / "out" / "conversation_run_manifest.json").read_text())
    assert manifest["completed_conversation_ids"] == [first.conversation_id]

    resumed = ConversationRunner(factory, tmp_path / "out", fixtures)
    results = resumed.run_suite([first, second], tmp_path / "work", resume=True)
    assert [result.success for result in results] == [True, True]
    assert len(calls) == 2


def test_resume_bootstraps_from_valid_summary_when_legacy_run_has_no_manifest(tmp_path):
    fixtures = tmp_path / "fixtures"; fixtures.mkdir(); (fixtures / "app.txt").write_text("base")
    calls = []

    def factory(workspace):
        calls.append(workspace)
        return _Session(workspace)

    task = _task()
    runner = ConversationRunner(factory, tmp_path / "out", fixtures)
    runner.run_task(task, tmp_path / "work" / task.conversation_id)
    assert not (tmp_path / "out" / "conversation_run_manifest.json").exists()

    results = ConversationRunner(factory, tmp_path / "out", fixtures).run_suite([task], tmp_path / "work", resume=True)
    assert results[0].success is True
    assert len(calls) == 1


def test_metrics_keep_explicit_numerators_and_partial_completion_depth(tmp_path):
    fixtures = tmp_path / "fixtures"; fixtures.mkdir(); (fixtures / "app.txt").write_text("base")
    result = ConversationRunner(lambda workspace: _Session(workspace, forget=True), tmp_path / "out", fixtures).run_task(_task(), tmp_path / "work")
    metrics = compute_conversation_metrics([result])
    assert metrics.conversation_success.numerator == 0
    assert metrics.constraint_retention.denominator == 1
    assert metrics.completion_depth == type(metrics.completion_depth)(1, 3)


def test_clarification_turn_rejects_edits_before_a_user_answer(tmp_path):
    fixtures = tmp_path / "fixtures"; fixtures.mkdir(); (fixtures / "app.txt").write_text("base")
    raw = _task().snapshot(); raw["turns"][0]["expects_clarification"] = True
    raw["verification_contract"]["clarification_patterns"] = {"t1": ["which"]}
    task = ConversationTaskSpec.from_dict(raw)
    result = ConversationRunner(lambda workspace: _Session(workspace, forget=True), tmp_path / "out", fixtures).run_task(task, tmp_path / "work")
    assert "premature_action" in result.failure_categories


def test_dev_manifest_covers_each_planned_category_and_has_valid_fixtures():
    root = Path(__file__).parents[1] / "coder_agent/eval/benchmarks/conversation/dev"
    tasks = load_conversation_tasks(root / "tasks.yaml")
    assert len(tasks) == 6
    assert {task.category for task in tasks} == {
        "clarification-before-action", "incremental-requirement", "user-correction",
        "regression-preservation", "contextual-reference", "scope-and-constraint-control",
    }
    for task in tasks:
        assert len(task.turns) == 3
        assert all((root / "fixtures" / relative).is_file() for relative in task.setup_files)


def test_freeze_manifest_rejects_fixture_or_task_changes(tmp_path):
    task_dir = tmp_path / "test"; fixtures = task_dir / "fixtures"; fixtures.mkdir(parents=True)
    (fixtures / "app.txt").write_text("base")
    (task_dir / "tasks.yaml").write_text("tasks:\n  - conversation_id: t\n    split: test\n    category: x\n    difficulty: x\n    setup_files: [app.txt]\n    max_total_steps: 3\n    verification_contract: {constraint_checks: {keep: [{cmd: 'true'}]}}\n    turns:\n      - {turn_id: a, user_message: x, intent: x, introduces_constraints: [keep]}\n      - {turn_id: b, user_message: x, intent: x, retains_constraints: [keep]}\n      - {turn_id: c, user_message: x, intent: x, retains_constraints: [keep]}\n    final_checks: []\n")
    manifest_path = tmp_path / "freeze.json"; manifest_path.write_text(json.dumps(build_freeze_manifest(task_dir, frozen_at="fixed")))
    assert validate_freeze_manifest(task_dir, manifest_path)["frozen_at"] == "fixed"
    (fixtures / "app.txt").write_text("changed")
    with pytest.raises(ValueError, match="fixture_tree"):
        validate_freeze_manifest(task_dir, manifest_path)


def test_checked_in_test_split_is_complete_and_category_balanced():
    root = Path(__file__).parents[1] / "coder_agent/eval/benchmarks/conversation/test"
    tasks = load_conversation_tasks(root / "tasks.yaml")
    assert len(tasks) == 12
    assert {task.category for task in tasks} == {
        "clarification-before-action", "incremental-requirement", "user-correction",
        "regression-preservation", "contextual-reference", "scope-and-constraint-control",
    }
    assert all(task.split == "test" and any(turn.retains_constraints for turn in task.turns) for task in tasks)
    assert all((root / "fixtures" / relative).is_file() for task in tasks for relative in task.setup_files)
    assert validate_freeze_manifest(root, root / "freeze.json")["task_ids"] == [task.conversation_id for task in tasks]
