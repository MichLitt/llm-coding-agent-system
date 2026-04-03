"""Tests for v0.4.3 benchmark expansion.

Covers:
- Custom benchmark expansion (21 → 40 tasks)
- MBPP benchmark module (unit tests with mocked HuggingFace)
- eval_verification MBPP hook
- run_ablation CLI MBPP choice
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helper paths
# ---------------------------------------------------------------------------
_SETUP_FILES_DIR = (
    Path(__file__).resolve().parents[1]
    / "coder_agent" / "eval" / "benchmarks" / "custom" / "setup_files"
)


# ---------------------------------------------------------------------------
# Section 1: Custom benchmark expansion
# ---------------------------------------------------------------------------

def test_load_custom_tasks_count_increased():
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    assert len(tasks) >= 40, f"Expected ≥40 tasks after expansion, got {len(tasks)}"


def test_task_ids_unique():
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    ids = [t.task_id for t in tasks]
    assert len(ids) == len(set(ids)), "Duplicate task_ids found"


def test_all_setup_files_exist():
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    missing = []
    for task in tasks:
        for sf in task.setup_files:
            # setup_files may be relative paths like "app/models.py"; check base name too
            candidate = _SETUP_FILES_DIR / sf
            if not candidate.exists():
                missing.append(f"{task.task_id}: {sf}")
    assert not missing, f"Missing setup files:\n" + "\n".join(missing)


def test_new_tasks_have_setup_files():
    """All new v0.4.3 tasks must have at least one setup_file (SE philosophy)."""
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    new_ids = [
        t.task_id for t in tasks
        if t.task_id.startswith("custom_medium_0") and int(t.task_id.split("_")[-1]) >= 6
        or t.task_id.startswith("custom_hard_0") and int(t.task_id.split("_")[-1]) >= 4
    ]
    tasks_by_id = {t.task_id: t for t in tasks}
    missing_setup = [tid for tid in new_ids if not tasks_by_id[tid].setup_files]
    assert not missing_setup, f"New tasks without setup_files: {missing_setup}"


def test_hard_tasks_max_steps_gte_15():
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    for t in tasks:
        if t.difficulty == "hard":
            assert t.max_steps >= 15, f"{t.task_id}: hard task max_steps={t.max_steps} < 15"


def test_medium_tasks_max_steps_range():
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    for t in tasks:
        if t.difficulty == "medium":
            assert 8 <= t.max_steps <= 20, (
                f"{t.task_id}: medium task max_steps={t.max_steps} out of range [8, 20]"
            )


def test_all_tasks_have_verification():
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    for t in tasks:
        assert len(t.verification) >= 1, f"{t.task_id}: no verification commands"


def test_verification_uses_portable_invocation():
    """Commands must use 'python -m pytest', not bare 'pytest'."""
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    bad = []
    for t in tasks:
        for check in t.verification:
            cmd = check.get("cmd", "")
            if cmd.startswith("pytest "):
                bad.append(f"{t.task_id}: {cmd!r}")
    assert not bad, f"Non-portable invocations found:\n" + "\n".join(bad)


def test_all_difficulties_valid():
    from coder_agent.eval.benchmarks.custom.loader import load_custom_tasks
    tasks = load_custom_tasks()
    for t in tasks:
        assert t.difficulty in ("easy", "medium", "hard"), (
            f"{t.task_id}: invalid difficulty {t.difficulty!r}"
        )


# ---------------------------------------------------------------------------
# Section 2: MBPP benchmark module (mocked HuggingFace)
# ---------------------------------------------------------------------------

def _make_fake_ds(n: int = 5):
    """Return a list of dicts mimicking the HF MBPP dataset."""
    items = []
    for i in range(n):
        items.append({
            "task_id": 100 + i,
            "text": f"Write a function that returns {i} squared.",
            "code": f"def f(): return {i*i}",
            "test_list": [f"assert f() == {i*i}"],
            "test_setup_code": "",
        })
    return items


def _write_fake_cache(path: Path, n: int = 5) -> None:
    lines = []
    for item in _make_fake_ds(n):
        lines.append(json.dumps(item))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_mbpp_load_returns_tasks(tmp_path):
    from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark, MBPPTask
    cache = tmp_path / "mbpp_data.jsonl"
    _write_fake_cache(cache, n=5)
    mb = MBPPBenchmark(data_path=cache)
    tasks = mb.load(limit=5)
    assert len(tasks) == 5
    assert all(isinstance(t, MBPPTask) for t in tasks)


def test_mbpp_task_ids_prefixed(tmp_path):
    from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark
    cache = tmp_path / "mbpp_data.jsonl"
    _write_fake_cache(cache)
    mb = MBPPBenchmark(data_path=cache)
    tasks = mb.load()
    assert all(t.task_id.startswith("mbpp_") for t in tasks)


def test_mbpp_build_agent_prompt_includes_asserts(tmp_path):
    from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark
    cache = tmp_path / "mbpp_data.jsonl"
    _write_fake_cache(cache, n=1)
    mb = MBPPBenchmark(data_path=cache)
    task = mb.load()[0]
    prompt = mb.build_agent_prompt(task)
    assert "assert" in prompt
    assert "solution.py" in prompt


def test_mbpp_to_task_spec_structure(tmp_path):
    from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark
    from coder_agent.eval.runner import TaskSpec
    cache = tmp_path / "mbpp_data.jsonl"
    _write_fake_cache(cache, n=1)
    mb = MBPPBenchmark(data_path=cache)
    task = mb.load()[0]
    spec = mb.to_task_spec(task)
    assert isinstance(spec, TaskSpec)
    assert spec.metadata["benchmark"] == "mbpp"
    assert "test_list" in spec.metadata
    assert spec.verification_contract["mode"] == "mbpp_official"


def test_mbpp_evaluate_correct_solution(tmp_path):
    from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark, MBPPTask
    mb = MBPPBenchmark(data_path=tmp_path / "dummy.jsonl")
    task = MBPPTask(
        task_id="mbpp_test",
        text="Return the sum of two numbers",
        code="def add(a, b): return a + b",
        test_list=["assert add(2, 3) == 5", "assert add(0, 0) == 0"],
    )
    assert mb.evaluate_solution(task, "def add(a, b):\n    return a + b") is True


def test_mbpp_evaluate_wrong_solution(tmp_path):
    from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark, MBPPTask
    mb = MBPPBenchmark(data_path=tmp_path / "dummy.jsonl")
    task = MBPPTask(
        task_id="mbpp_test",
        text="Return the sum",
        code="def add(a, b): return a + b",
        test_list=["assert add(2, 3) == 5"],
    )
    assert mb.evaluate_solution(task, "def add(a, b):\n    return a - b") is False


def test_mbpp_evaluate_empty_solution(tmp_path):
    from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark, MBPPTask
    mb = MBPPBenchmark(data_path=tmp_path / "dummy.jsonl")
    task = MBPPTask(task_id="t", text="x", code="", test_list=["assert True"])
    assert mb.evaluate_solution(task, "") is False


# ---------------------------------------------------------------------------
# Section 3: eval_verification MBPP hook
# ---------------------------------------------------------------------------

def test_run_mbpp_check_missing_file(tmp_path):
    from coder_agent.eval.eval_verification import run_mbpp_check
    from coder_agent.eval.runner import TaskSpec

    task = TaskSpec(
        task_id="mbpp_1",
        description="test",
        metadata={"benchmark": "mbpp", "test_list": ["assert True"], "test_setup_code": ""},
        verification_contract={"mode": "mbpp_official"},
    )
    checks_passed, error = run_mbpp_check(task, tmp_path)
    assert checks_passed == 0
    assert error is not None


def test_run_mbpp_check_correct_solution(tmp_path):
    from coder_agent.eval.eval_verification import run_mbpp_check
    from coder_agent.eval.runner import TaskSpec

    (tmp_path / "solution.py").write_text("def add(a, b):\n    return a + b\n")
    task = TaskSpec(
        task_id="mbpp_1",
        description="test",
        metadata={
            "benchmark": "mbpp",
            "task_id": "mbpp_1",
            "text": "Add two numbers",
            "test_list": ["assert add(1, 2) == 3", "assert add(0, 0) == 0"],
            "test_setup_code": "",
        },
        verification_contract={"mode": "mbpp_official"},
    )
    checks_passed, error = run_mbpp_check(task, tmp_path)
    assert checks_passed == 1
    assert error is None


def test_build_verification_hook_mbpp(tmp_path):
    from coder_agent.eval.eval_verification import build_verification_hook
    from coder_agent.eval.runner import TaskSpec

    task = TaskSpec(
        task_id="mbpp_1",
        description="test",
        metadata={"benchmark": "mbpp", "task_id": "mbpp_1", "text": "",
                  "test_list": [], "test_setup_code": ""},
        verification_contract={"mode": "mbpp_official"},
    )
    hook = build_verification_hook(task, tmp_path)
    assert hook is not None, "build_verification_hook should return a callable for mbpp_official"
    assert callable(hook)


# ---------------------------------------------------------------------------
# Section 4: CLI accepts mbpp benchmark option
# ---------------------------------------------------------------------------

def test_run_ablation_accepts_mbpp_benchmark():
    result = subprocess.run(
        [sys.executable, "-m", "coder_agent.cli.run_ablation", "--help"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0
    assert "mbpp" in result.stdout, "CLI --help should list 'mbpp' as a benchmark option"
