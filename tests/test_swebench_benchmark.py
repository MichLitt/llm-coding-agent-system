from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import MethodType

import pytest

from coder_agent.config import cfg
from coder_agent.eval.benchmarks.swebench.adapter import (
    extract_patch,
    prepare_swebench_workspace,
    run_swebench_test_command,
)
from coder_agent.eval.benchmarks.swebench.loader import load_swebench_tasks
from coder_agent.eval.benchmarks.swebench.manifest_export import export_official_manifest
from coder_agent.eval.eval_verification import run_swebench_check
from coder_agent.eval.metrics import EvalResult
from coder_agent.eval.runner import EvalRunner, TaskSpec


_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "coder_agent" / "eval" / "benchmarks" / "swebench" / "fixtures"
_SWEBENCH_ROOT = Path(__file__).resolve().parents[1] / "coder_agent" / "eval" / "benchmarks" / "swebench"
_OFFICIAL_SOURCE_PATH = _SWEBENCH_ROOT / "official_tasks.source.json"
_OFFICIAL_MANIFEST_PATH = _SWEBENCH_ROOT / "official_manifest.generated.json"
_LOCAL_OVERRIDES_PATH = _SWEBENCH_ROOT / "local_overrides.json"
_EXPECTED_SMOKE_IDS = [
    "pylint-dev__pylint-5859",
    "sympy__sympy-22005",
    "pytest-dev__pytest-7220",
]
_EXPECTED_PROMOTED_IDS = {
    "pylint-dev__pylint-5859",
    "pylint-dev__pylint-7993",
    "sympy__sympy-22005",
    "sympy__sympy-21627",
    "pytest-dev__pytest-7220",
    "pytest-dev__pytest-7373",
    "sphinx-doc__sphinx-8273",
    "pallets__flask-4992",
}
_EXPECTED_PROMOTED_REPOS = {
    "pylint-dev/pylint",
    "sympy/sympy",
    "pytest-dev/pytest",
    "sphinx-doc/sphinx",
    "pallets/flask",
}


class DummyAgent:
    def __init__(self):
        self.close_calls = 0

    def reset(self) -> None:
        return None

    def close(self) -> None:
        self.close_calls += 1

    def run(self, *args, **kwargs):
        return type(
            "TurnResult",
            (),
            {
                "final_status": "success",
                "steps": 1,
                "retry_steps": 0,
                "termination_reason": "model_stop",
                "total_tokens": 10,
                "trajectory_id": None,
                "error_details": [],
            },
        )()


def _git_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(path),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _build_local_repo(tmp_path: Path, fixture_name: str) -> tuple[Path, str]:
    source = _FIXTURE_ROOT / fixture_name
    repo = tmp_path / f"{fixture_name}-repo"
    shutil.copytree(source, repo)
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test Bot"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test Bot",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_NAME": "Test Bot",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    }
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "base commit"], cwd=str(repo), check=True, env=env)
    return repo, _git_head(repo)


def _git_commit_all(path: Path, message: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test Bot",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_NAME": "Test Bot",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    }
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=str(path), check=True, env=env)
    return _git_head(path)


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _local_swebench_files(
    tmp_path: Path,
    *,
    official_tasks: list[dict],
    overrides: list[dict],
) -> tuple[Path, Path]:
    official_manifest_path = _write_json(
        tmp_path / "official_manifest.generated.json",
        {
            "dataset_name": "princeton-nlp/SWE-bench_Lite",
            "dataset_version": "test-fixture",
            "manifest_version": 1,
            "source_mode": "official_lite_generated_v1",
            "source_source": "https://example.invalid/source",
            "source_revision": "test-revision",
            "generated_at": "2026-04-07T00:00:00Z",
            "generator_version": "test",
            "tasks": official_tasks,
        },
    )
    overrides_path = _write_json(
        tmp_path / "local_overrides.json",
        {
            "manifest_version": 1,
            "overrides": overrides,
        },
    )
    return official_manifest_path, overrides_path


def _local_task_files(tmp_path: Path, *, fixture_name: str, task_id: str, subset: str) -> tuple[Path, Path]:
    repo_path, base_commit = _build_local_repo(tmp_path, fixture_name)
    if fixture_name == "calc_bug":
        official_task = {
            "task_id": task_id,
            "instance_id": task_id,
            "repo": "local/calc_bug",
            "repo_url": str(repo_path),
            "base_commit": base_commit,
            "problem_statement": "Fix calculator add.",
            "environment_setup_commit": base_commit,
            "fail_to_pass": [
                "tests/test_calculator.py::test_add_positive_numbers",
                "tests/test_calculator.py::test_add_negative_numbers",
            ],
            "pass_to_pass": [],
            "test_patch": "",
        }
        override = {
            "instance_id": task_id,
            "subset": subset,
            "python_version": "3.10",
            "setup_commands": [
                "python -c \"from pathlib import Path; Path('setup.marker').write_text('ready', encoding='utf-8')\""
            ],
            "test_command_override": "python -m pytest -q",
            "expected_patch_targets": ["app/calculator.py"],
        }
    else:
        official_task = {
            "task_id": task_id,
            "instance_id": task_id,
            "repo": "local/status_bug",
            "repo_url": str(repo_path),
            "base_commit": base_commit,
            "problem_statement": "Fix degraded status rendering.",
            "environment_setup_commit": base_commit,
            "fail_to_pass": [
                "tests/test_service.py::test_unhealthy_checks_report_degraded",
            ],
            "pass_to_pass": [
                "tests/test_service.py::test_healthy_checks_report_ok",
            ],
            "test_patch": "",
        }
        override = {
            "instance_id": task_id,
            "subset": subset,
            "setup_commands": [
                "python -c \"from pathlib import Path; Path('setup.marker').write_text('ready', encoding='utf-8')\""
            ],
            "test_command_override": "python -m pytest -q",
            "expected_patch_targets": ["app/http.py", "app/service.py"],
        }
    return _local_swebench_files(
        tmp_path,
        official_tasks=[official_task],
        overrides=[override],
    )


def _write_status_fix(workspace: Path) -> None:
    (workspace / "app" / "http.py").write_text(
        "def build_status_response(payload: dict, healthy: bool) -> dict:\n"
        '    status = "ok" if healthy else "degraded"\n'
        '    return {"status": status, "payload": payload}\n',
        encoding="utf-8",
    )


def test_load_swebench_tasks_by_subset():
    smoke_tasks = load_swebench_tasks(subset="smoke")
    promoted_tasks = load_swebench_tasks(subset="promoted")

    assert [task.task_id for task in smoke_tasks] == _EXPECTED_SMOKE_IDS
    assert {task.task_id for task in promoted_tasks} == _EXPECTED_PROMOTED_IDS
    assert all(task.metadata["source_mode"] == "official_lite_generated_v1" for task in smoke_tasks + promoted_tasks)
    assert all(task.metadata["repo_url"].startswith("https://github.com/") for task in smoke_tasks + promoted_tasks)
    assert smoke_tasks[0].metadata["setup_commands"]
    assert smoke_tasks[0].metadata["python_version"] == "3.10"
    assert "-p no:benchmark" in smoke_tasks[0].metadata["test_command"]
    assert smoke_tasks[0].metadata["verification_files"] == ["tests/checkers/unittest_misc.py"]
    assert smoke_tasks[0].metadata["authorized_test_edit_paths"] == ["tests/checkers/unittest_misc.py"]
    assert smoke_tasks[0].metadata["setup_complexity"] == "medium"
    assert smoke_tasks[0].metadata["expected_patch_target_count"] == 2
    assert smoke_tasks[0].metadata["test_patch"]
    assert smoke_tasks[0].metadata["official_manifest_sha256"]
    assert smoke_tasks[0].metadata["overrides_manifest_sha256"]
    assert {task.metadata["repo"] for task in promoted_tasks} == _EXPECTED_PROMOTED_REPOS
    versions = {task.task_id: task.metadata["python_version"] for task in promoted_tasks}
    assert versions == {
        "pylint-dev__pylint-5859": "3.10",
        "pylint-dev__pylint-7993": "3.10",
        "sympy__sympy-22005": "3.10",
        "sympy__sympy-21627": "3.10",
        "pytest-dev__pytest-7220": "3.9",
        "pytest-dev__pytest-7373": "3.9",
        "sphinx-doc__sphinx-8273": "3.10",
        "pallets__flask-4992": "3.11",
    }
    assert all(task.metadata["setup_commands"] for task in promoted_tasks)
    assert all(task.metadata["primary_failure_mode_category"] for task in promoted_tasks)
    pylint_smoke_task = next(task for task in promoted_tasks if task.task_id == "pylint-dev__pylint-5859")
    assert pylint_smoke_task.metadata["authorized_test_edit_paths"] == ["tests/checkers/unittest_misc.py"]
    assert pylint_smoke_task.metadata["subset_membership"] == ["smoke", "promoted"]
    assert any("setuptools wheel" in command for command in pylint_smoke_task.metadata["setup_commands"])
    assert any("--no-build-isolation" in command for command in pylint_smoke_task.metadata["setup_commands"])
    pylint_reporting_task = next(task for task in promoted_tasks if task.task_id == "pylint-dev__pylint-7993")
    assert pylint_reporting_task.metadata["authorized_test_edit_paths"] == ["tests/reporters/unittest_reporting.py"]
    assert pylint_reporting_task.metadata["test_command"] == "python -m pytest -q -p no:benchmark tests/reporters/unittest_reporting.py"
    assert pylint_reporting_task.metadata["expected_patch_targets"] == [
        "pylint/reporters/text.py",
        "tests/reporters/unittest_reporting.py",
    ]
    sympy_task = next(task for task in promoted_tasks if task.task_id == "sympy__sympy-22005")
    assert sympy_task.metadata["test_command"] == "python -m pytest -q sympy/solvers/tests/test_polysys.py"
    assert sympy_task.metadata["authorized_test_edit_paths"] == []
    sympy_complex_task = next(task for task in promoted_tasks if task.task_id == "sympy__sympy-21627")
    assert sympy_complex_task.metadata["test_command"] == "python -m pytest -q sympy/functions/elementary/tests/test_complexes.py"
    assert sympy_complex_task.metadata["authorized_test_edit_paths"] == ["sympy/functions/elementary/tests/test_complexes.py"]
    pytest_task = next(task for task in promoted_tasks if task.task_id == "pytest-dev__pytest-7220")
    assert any("xmlschema" in command and "hypothesis" in command for command in pytest_task.metadata["setup_commands"])
    assert pytest_task.metadata["test_command"] == "python -m pytest -q testing/test_nodes.py"
    assert pytest_task.metadata["subset_membership"] == ["smoke", "promoted"]
    assert pytest_task.metadata["authorized_test_edit_paths"] == []
    pytest_mark_task = next(task for task in promoted_tasks if task.task_id == "pytest-dev__pytest-7373")
    assert pytest_mark_task.metadata["test_command"] == "python -m pytest -q testing/test_mark.py"
    assert pytest_mark_task.metadata["authorized_test_edit_paths"] == ["testing/test_mark.py"]
    sphinx_task = next(task for task in promoted_tasks if task.task_id == "sphinx-doc__sphinx-8273")
    assert any("setuptools<81" in command and "jinja2<3.1" in command for command in sphinx_task.metadata["setup_commands"])
    assert any("roman" in command for command in sphinx_task.metadata["setup_commands"])
    assert sphinx_task.metadata["test_command"] == "python -m pytest -q tests/test_build_manpage.py"
    assert sphinx_task.metadata["authorized_test_edit_paths"] == []
    flask_task = next(task for task in promoted_tasks if task.task_id == "pallets__flask-4992")
    assert flask_task.metadata["test_command"] == "python -m pytest -q tests/test_config.py"
    assert flask_task.metadata["authorized_test_edit_paths"] == [
        "tests/test_config.py",
        "tests/static/config.toml",
    ]
    assert any("requirements/tests.txt" in command for command in flask_task.metadata["setup_commands"])
    assert any("Werkzeug<2.3" in command for command in flask_task.metadata["setup_commands"])
    assert all(task.metadata["official_manifest_sha256"] for task in smoke_tasks + promoted_tasks)


def test_checked_in_swebench_source_manifest_and_overrides_stay_aligned():
    source = json.loads(_OFFICIAL_SOURCE_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(_OFFICIAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    overrides = json.loads(_LOCAL_OVERRIDES_PATH.read_text(encoding="utf-8"))

    source_ids = {task["instance_id"] for task in source["tasks"]}
    manifest_ids = {task["task_id"] for task in manifest["tasks"]}
    promoted_override_ids = {
        override["instance_id"]
        for override in overrides["overrides"]
        if "promoted" in (
            override["subset"]
            if isinstance(override["subset"], list)
            else [override["subset"]]
        )
    }

    assert source_ids == _EXPECTED_PROMOTED_IDS
    assert manifest_ids == _EXPECTED_PROMOTED_IDS
    assert promoted_override_ids == _EXPECTED_PROMOTED_IDS
    assert promoted_override_ids <= source_ids
    assert promoted_override_ids <= manifest_ids
    assert {task["repo"] for task in manifest["tasks"]} == _EXPECTED_PROMOTED_REPOS


def test_load_swebench_tasks_requires_instance_and_test_lists(tmp_path):
    official_manifest_path, overrides_path = _local_swebench_files(
        tmp_path,
        official_tasks=[
            {
                "task_id": "broken",
                "repo": "demo/repo",
                "repo_url": "https://github.com/demo/repo.git",
                "base_commit": "abc",
                "problem_statement": "demo",
                "environment_setup_commit": "abc",
                "pass_to_pass": [],
                "test_patch": "",
            }
        ],
        overrides=[{"instance_id": "broken", "subset": "smoke"}],
    )

    with pytest.raises(ValueError, match="instance_id, fail_to_pass"):
        load_swebench_tasks(
            subset="smoke",
            official_manifest_path=official_manifest_path,
            overrides_path=overrides_path,
        )


def test_prepare_swebench_workspace_clones_expected_base_commit(tmp_path):
    official_manifest_path, overrides_path = _local_task_files(
        tmp_path, fixture_name="calc_bug", task_id="calc_local", subset="smoke"
    )
    task = load_swebench_tasks(
        subset="smoke",
        official_manifest_path=official_manifest_path,
        overrides_path=overrides_path,
    )[0]
    workspace = tmp_path / task.task_id

    prepared = prepare_swebench_workspace(task, workspace)

    assert prepared == workspace
    assert (workspace / ".git").is_dir()
    assert (workspace / "app" / "calculator.py").exists()
    assert (workspace / "setup.marker").read_text(encoding="utf-8") == "ready"
    assert _git_head(workspace) == task.metadata["base_commit"]


def test_prepare_swebench_workspace_renders_python_version_in_setup_command(tmp_path, monkeypatch):
    task = TaskSpec(
        task_id="render_setup",
        description="demo",
        metadata={
            "benchmark": "swebench",
            "repo_url": str(tmp_path / "missing"),
            "base_commit": "abc",
            "python_version": "3.10",
            "setup_commands": ["uv venv --python {python_version} .swebench-venv"],
        },
    )
    calls: list[str] = []

    monkeypatch.setattr(
        "coder_agent.eval.benchmarks.swebench.adapter._clone_source_for_task",
        lambda _: str(tmp_path / "source"),
    )
    monkeypatch.setattr(
        "coder_agent.eval.benchmarks.swebench.adapter._run_git",
        lambda args, cwd=None: "abc" if args[:2] == ["rev-parse", "HEAD"] else "",
    )
    monkeypatch.setattr(
        "coder_agent.eval.benchmarks.swebench.adapter._run_setup_command",
        lambda command, workspace, task_id: calls.append(command),
    )

    prepare_swebench_workspace(task, tmp_path / "workspace")
    assert calls == ["uv venv --python 3.10 .swebench-venv"]


def test_prepare_swebench_workspace_uses_mirror_path_when_present(tmp_path):
    repo_path, base_commit = _build_local_repo(tmp_path, "calc_bug")
    task = TaskSpec(
        task_id="mirror_clone",
        description="demo",
        metadata={
            "benchmark": "swebench",
            "repo_url": "https://github.com/example/will-not-be-used.git",
            "mirror_path": str(repo_path),
            "base_commit": base_commit,
            "test_command": "python -m pytest -q",
            "fail_to_pass": [],
            "pass_to_pass": [],
        },
    )

    workspace = prepare_swebench_workspace(task, tmp_path / "workspace")
    assert _git_head(workspace) == base_commit
    assert (workspace / "app" / "calculator.py").exists()


def test_prepare_swebench_workspace_falls_back_to_repo_url(tmp_path):
    repo_path, base_commit = _build_local_repo(tmp_path, "calc_bug")
    task = TaskSpec(
        task_id="repo_url_clone",
        description="demo",
        metadata={
            "benchmark": "swebench",
            "repo_url": str(repo_path),
            "base_commit": base_commit,
            "test_command": "python -m pytest -q",
            "fail_to_pass": [],
            "pass_to_pass": [],
        },
    )

    workspace = prepare_swebench_workspace(task, tmp_path / "workspace")
    assert _git_head(workspace) == base_commit
    assert (workspace / "app" / "calculator.py").exists()


def test_swebench_test_command_and_patch_extraction(tmp_path):
    official_manifest_path, overrides_path = _local_task_files(
        tmp_path, fixture_name="calc_bug", task_id="calc_local", subset="smoke"
    )
    task = load_swebench_tasks(
        subset="smoke",
        official_manifest_path=official_manifest_path,
        overrides_path=overrides_path,
    )[0]
    workspace = prepare_swebench_workspace(task, tmp_path / task.task_id)

    failed, message = run_swebench_test_command(task.metadata["test_command"], workspace)
    assert failed is False
    assert "FAILED" in message or "assert" in message.lower()

    calculator = workspace / "app" / "calculator.py"
    calculator.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

    passed, message = run_swebench_test_command(task.metadata["test_command"], workspace)
    assert passed is True
    assert message == ""

    patch_text = extract_patch(workspace)
    assert "app/calculator.py" in patch_text


def test_run_swebench_check_uses_fail_and_pass_lists(tmp_path):
    official_manifest_path, overrides_path = _local_task_files(
        tmp_path, fixture_name="status_bug", task_id="status_local", subset="promoted"
    )
    task = load_swebench_tasks(
        subset="promoted",
        official_manifest_path=official_manifest_path,
        overrides_path=overrides_path,
    )[0]
    workspace = prepare_swebench_workspace(task, tmp_path / task.task_id)

    checks_passed, message = run_swebench_check(task, workspace)
    assert checks_passed == 0
    assert "fail_to_pass still failing" in (message or "")

    _write_status_fix(workspace)
    checks_passed, message = run_swebench_check(task, workspace)
    assert checks_passed == 1
    assert message is None


def test_run_swebench_check_detects_pass_to_pass_regression(tmp_path):
    official_manifest_path, overrides_path = _local_task_files(
        tmp_path, fixture_name="status_bug", task_id="status_local", subset="promoted"
    )
    task = load_swebench_tasks(
        subset="promoted",
        official_manifest_path=official_manifest_path,
        overrides_path=overrides_path,
    )[0]
    workspace = prepare_swebench_workspace(task, tmp_path / task.task_id)

    (workspace / "app" / "service.py").write_text(
        "from app.http import build_status_response\n\n"
        "def get_system_status(checks: list[dict]) -> dict:\n"
        "    payload = {\n"
        '        "checks": checks,\n'
        '        "healthy": False,\n'
        "    }\n"
        "    return build_status_response(payload, healthy=False)\n",
        encoding="utf-8",
    )
    _write_status_fix(workspace)

    checks_passed, message = run_swebench_check(task, workspace)
    assert checks_passed == 0
    assert "pass_to_pass regressed" in (message or "")


def test_run_swebench_check_applies_verification_overlay_without_polluting_patch(tmp_path):
    repo_path, base_commit = _build_local_repo(tmp_path, "calc_bug")
    test_file = repo_path / "tests" / "test_calculator.py"
    original_test_text = test_file.read_text(encoding="utf-8")
    updated_test_text = (
        "from app.calculator import add\n\n"
        "def test_add_positive_numbers():\n"
        "    assert add(2, 3) == 5\n\n"
        "def test_add_negative_numbers():\n"
        "    assert add(-4, 1) == -3\n\n"
        "def test_add_zero_numbers():\n"
        "    assert add(0, 0) == 0\n"
    )
    test_file.write_text(updated_test_text, encoding="utf-8")
    _git_commit_all(repo_path, "add overlay regression test")
    test_patch = subprocess.run(
        ["git", "diff", base_commit, "HEAD", "--", "tests/test_calculator.py"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    test_file.write_text(original_test_text, encoding="utf-8")
    official_manifest_path, overrides_path = _local_swebench_files(
        tmp_path,
        official_tasks=[
            {
                "task_id": "calc_overlay",
                "instance_id": "calc_overlay",
                "repo": "local/calc_bug",
                "repo_url": str(repo_path),
                "base_commit": base_commit,
                "problem_statement": "Fix calculator add and keep tests green.",
                "environment_setup_commit": base_commit,
                "fail_to_pass": ["tests/test_calculator.py::test_add_zero_numbers"],
                "pass_to_pass": ["tests/test_calculator.py::test_add_positive_numbers"],
                "test_patch": test_patch,
            }
        ],
        overrides=[
            {
                "instance_id": "calc_overlay",
                "subset": "smoke",
                "test_command_override": "python -m pytest -q tests/test_calculator.py",
                "expected_patch_targets": ["app/calculator.py", "tests/test_calculator.py"],
            }
        ],
    )
    task = load_swebench_tasks(
        subset="smoke",
        official_manifest_path=official_manifest_path,
        overrides_path=overrides_path,
    )[0]
    workspace = prepare_swebench_workspace(task, tmp_path / task.task_id)

    checks_passed, message = run_swebench_check(task, workspace)
    assert checks_passed == 0
    assert "fail_to_pass still failing" in (message or "")

    (workspace / "app" / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    checks_passed, message = run_swebench_check(task, workspace)
    assert checks_passed == 1
    assert message is None

    patch_text = (workspace / "agent.patch").read_text(encoding="utf-8")
    assert "app/calculator.py" in patch_text
    assert "tests/test_calculator.py" not in patch_text


def test_run_swebench_check_applies_overlay_when_agent_created_same_untracked_file(tmp_path):
    repo_path, base_commit = _build_local_repo(tmp_path, "calc_bug")
    overlay_file = repo_path / "tests" / "static" / "config.toml"
    overlay_file.parent.mkdir(parents=True, exist_ok=True)
    overlay_file.write_text('TEST_KEY = "official"\n', encoding="utf-8")
    _git_commit_all(repo_path, "add overlay-only regression file")
    test_patch = subprocess.run(
        ["git", "diff", base_commit, "HEAD", "--", "tests/static/config.toml"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    official_manifest_path, overrides_path = _local_swebench_files(
        tmp_path,
        official_tasks=[
            {
                "task_id": "calc_overlay_new_file",
                "instance_id": "calc_overlay_new_file",
                "repo": "local/calc_bug",
                "repo_url": str(repo_path),
                "base_commit": base_commit,
                "problem_statement": "Keep verification overlay working when the agent created the same regression file.",
                "environment_setup_commit": base_commit,
                "fail_to_pass": [],
                "pass_to_pass": [],
                "test_patch": test_patch,
            }
        ],
        overrides=[
            {
                "instance_id": "calc_overlay_new_file",
                "subset": "smoke",
                "test_command_override": "python -m pytest -q tests/test_calculator.py",
                "expected_patch_targets": ["app/calculator.py", "tests/static/config.toml"],
                "authorized_test_edit_paths": ["tests/static/config.toml"],
            }
        ],
    )
    task = load_swebench_tasks(
        subset="smoke",
        official_manifest_path=official_manifest_path,
        overrides_path=overrides_path,
    )[0]
    workspace = prepare_swebench_workspace(task, tmp_path / task.task_id)
    (workspace / "app" / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    agent_version = 'TEST_KEY = "agent"\n'
    created_path = workspace / "tests" / "static" / "config.toml"
    created_path.parent.mkdir(parents=True, exist_ok=True)
    created_path.write_text(agent_version, encoding="utf-8")

    checks_passed, message = run_swebench_check(task, workspace)

    assert checks_passed == 1
    assert message is None
    assert created_path.read_text(encoding="utf-8") == agent_version


def test_export_official_manifest_generates_stable_checked_in_shape(tmp_path):
    source_path = _write_json(
        tmp_path / "official_tasks.source.json",
        {
            "dataset_name": "princeton-nlp/SWE-bench_Lite",
            "dataset_version": "snapshot",
            "source_source": "https://example.invalid/source",
            "source_revision": "abc123",
            "exported_at": "2026-04-07T00:00:00Z",
            "generator_version": "test",
            "tasks": [
                {
                    "instance_id": "demo__repo-1",
                    "repo": "demo/repo",
                    "base_commit": "deadbeef",
                    "problem_statement": "Fix demo bug.",
                    "environment_setup_commit": "deadbeef",
                    "FAIL_TO_PASS": ["tests/test_demo.py::test_bug"],
                    "PASS_TO_PASS": ["tests/test_demo.py::test_ok"],
                    "test_patch": "",
                }
            ],
        },
    )
    output_path = tmp_path / "official_manifest.generated.json"

    manifest = export_official_manifest(source_path=source_path, output_path=output_path)

    assert manifest["source_mode"] == "official_lite_generated_v1"
    assert manifest["tasks"][0]["task_id"] == "demo__repo-1"
    assert manifest["tasks"][0]["repo_url"] == "https://github.com/demo/repo.git"
    assert manifest["tasks"][0]["fail_to_pass"] == ["tests/test_demo.py::test_bug"]
    assert output_path.exists()


def test_load_swebench_tasks_rejects_override_of_official_fields(tmp_path):
    official_manifest_path, overrides_path = _local_swebench_files(
        tmp_path,
        official_tasks=[
            {
                "task_id": "demo__repo-1",
                "instance_id": "demo__repo-1",
                "repo": "demo/repo",
                "repo_url": "https://github.com/demo/repo.git",
                "base_commit": "abc",
                "problem_statement": "Fix demo bug.",
                "environment_setup_commit": "abc",
                "fail_to_pass": [],
                "pass_to_pass": [],
                "test_patch": "",
            }
        ],
        overrides=[
            {
                "instance_id": "demo__repo-1",
                "subset": ["smoke", "promoted"],
                "base_commit": "override-not-allowed",
            }
        ],
    )

    with pytest.raises(ValueError, match="cannot override official field"):
        load_swebench_tasks(
            subset="smoke",
            official_manifest_path=official_manifest_path,
            overrides_path=overrides_path,
        )


def test_run_suite_uses_task_scoped_workspace_for_swebench(tmp_path):
    tasks = load_swebench_tasks(subset="promoted")
    captured_workspaces: list[Path] = []
    runner = EvalRunner(
        agent_factory=lambda _, workspace: captured_workspaces.append(workspace) or DummyAgent(),
        output_dir=tmp_path,
    )
    run_ids = iter(["20260407120000-aaaabbbb"])
    runner._allocate_run_id = lambda: next(run_ids)

    def fake_run_task(self, task, agent, config_label="", workspace=None, run_id=None):
        return EvalResult(
            task_id=task.task_id,
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

    runner.run_task = MethodType(fake_run_task, runner)
    runner.run_suite(tasks, config_label="swe_demo", benchmark_name="swebench", preset="C3", verbose=False)

    expected_root = cfg.agent.workspace.resolve() / "swe_demo" / "20260407120000-aaaabbbb"
    assert captured_workspaces == [expected_root / task.task_id for task in tasks]


def test_swebench_manifest_records_benchmark_metadata(tmp_path):
    tasks = [load_swebench_tasks(subset="smoke")[0]]
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)
    runner._allocate_run_id = lambda: "20260407120000-aaaabbbb"

    def fake_run_task(self, task, agent, config_label="", workspace=None, run_id=None):
        return EvalResult(
            task_id=task.task_id,
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

    runner.run_task = MethodType(fake_run_task, runner)
    runner.run_suite(tasks, config_label="swe_meta", benchmark_name="swebench", preset="C3", verbose=False)

    manifest = json.loads((tmp_path / "swe_meta_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["benchmark_metadata"]["dataset_name"] == "princeton-nlp/SWE-bench_Lite"
    assert manifest["benchmark_metadata"]["source_mode"] == "official_lite_generated_v1"
    assert manifest["benchmark_metadata"]["subset"] == "smoke"
    assert manifest["benchmark_metadata"]["official_manifest_sha256"]
    assert manifest["benchmark_metadata"]["overrides_manifest_sha256"]
    assert manifest["benchmark_metadata_sha256"]


def test_swebench_resume_rejects_benchmark_metadata_mismatch(tmp_path):
    tasks = [load_swebench_tasks(subset="smoke")[0]]
    runner = EvalRunner(agent_factory=lambda _, __: DummyAgent(), output_dir=tmp_path)
    runner._allocate_run_id = lambda: "20260407120000-aaaabbbb"

    def fake_run_task(self, task, agent, config_label="", workspace=None, run_id=None):
        return EvalResult(
            task_id=task.task_id,
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

    runner.run_task = MethodType(fake_run_task, runner)
    runner.run_suite(tasks, config_label="swe_resume", benchmark_name="swebench", preset="C3", verbose=False)

    manifest_path = tmp_path / "swe_resume_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["benchmark_metadata"]["subset"] = "promoted"
    manifest["benchmark_metadata_sha256"] = "different"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="benchmark_metadata_sha256 mismatch"):
        runner.run_suite(tasks, config_label="swe_resume", benchmark_name="swebench", preset="C3", resume=True, verbose=False)
