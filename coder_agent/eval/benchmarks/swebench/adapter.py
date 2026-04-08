"""Workspace adapter for official SWE-bench tasks."""

from __future__ import annotations

from contextlib import contextmanager
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from coder_agent.core.workspace_env import workspace_command_env, workspace_python_executable


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "git command failed").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {message}")
    return result.stdout.strip()


def _normalize_command(command: str) -> str:
    return _normalize_command_for_workspace(command, Path.cwd())


def _normalize_command_for_workspace(command: str, workspace: Path) -> str:
    python_exe = str(workspace_python_executable(workspace))
    if command.startswith("python "):
        return f'"{python_exe}" {command[len("python "):]}'
    if command.startswith("pytest "):
        return f'"{python_exe}" -m {command}'
    return command


def _clone_source_for_task(task: Any) -> str:
    mirror_value = task.metadata.get("mirror_path")
    mirror_path_raw = str(mirror_value).strip() if mirror_value not in (None, "") else ""
    if mirror_path_raw:
        mirror_path = Path(mirror_path_raw).resolve()
        if not mirror_path.exists():
            raise ValueError(f"SWE-bench mirror_path does not exist: {mirror_path}")
        return str(mirror_path)

    repo_value = task.metadata.get("repo_url")
    repo_url = str(repo_value).strip() if repo_value not in (None, "") else ""
    if repo_url:
        return repo_url
    raise ValueError(f"SWE-bench task {task.task_id} missing repo_url or mirror_path metadata")


def prepare_swebench_workspace(task: Any, workspace: Path) -> Path:
    clone_source = _clone_source_for_task(task)
    expected_commit = str(task.metadata.get("base_commit", "")).strip()
    if not expected_commit:
        raise ValueError(f"SWE-bench task {task.task_id} missing base_commit metadata")

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)

    _run_git(["clone", "-q", clone_source, str(workspace)])
    _run_git(["checkout", "-q", expected_commit], workspace)
    actual_commit = _run_git(["rev-parse", "HEAD"], workspace)
    if actual_commit != expected_commit:
        raise ValueError(
            f"SWE-bench task {task.task_id} base_commit mismatch after checkout: "
            f"expected {expected_commit}, got {actual_commit}"
        )
    setup_commands = [
        _render_setup_command(str(command).strip(), task)
        for command in task.metadata.get("setup_commands", [])
        if str(command).strip()
    ]
    for command in setup_commands:
        _run_setup_command(command, workspace, task_id=task.task_id)
    return workspace


def extract_patch(workspace: Path) -> str:
    return _run_git(["diff", "--binary", "--no-ext-diff", "HEAD"], workspace)


def write_patch_artifact(workspace: Path, patch_text: str) -> Path:
    patch_path = workspace / "agent.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    return patch_path


@contextmanager
def verification_overlay(
    workspace: Path,
    *,
    test_patch: str | None = None,
):
    patch_text = str(test_patch or "")
    if not patch_text.strip():
        yield
        return

    patch_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".patch",
            dir=workspace,
            delete=False,
        ) as handle:
            handle.write(patch_text)
            patch_path = handle.name
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "--recount", patch_path],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "git apply failed").strip()
            raise RuntimeError(f"verification test_patch apply failed: {message}")
        yield
    finally:
        if patch_path is not None and Path(patch_path).exists():
            subprocess.run(
                ["git", "apply", "-R", "--whitespace=nowarn", "--recount", patch_path],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=60,
            )
            os.unlink(patch_path)


def _clear_python_caches(workspace: Path) -> None:
    for cache_dir in workspace.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)


def _run_setup_command(command: str, workspace: Path, *, task_id: str) -> None:
    normalized = _normalize_command_for_workspace(command, workspace)
    try:
        result = subprocess.run(
            normalized,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=300,
            env=workspace_command_env(workspace),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"SWE-bench task {task_id} setup command timed out: {command}") from exc
    except Exception as exc:
        raise RuntimeError(f"SWE-bench task {task_id} setup command failed: {command}: {exc}") from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
        first_lines = "\n".join(message.splitlines()[:12]).strip()
        raise RuntimeError(
            f"SWE-bench task {task_id} setup command failed: {command}\n{first_lines}"
        )


def _render_setup_command(command: str, task: Any) -> str:
    python_version = str(task.metadata.get("python_version", "") or "").strip()
    if python_version:
        return command.replace("{python_version}", python_version)
    return command.replace(" --python {python_version}", "")


def run_swebench_test_command(
    test_command: str,
    workspace: Path,
    *,
    test_targets: list[str] | None = None,
) -> tuple[bool, str]:
    _clear_python_caches(workspace)
    normalized = _normalize_command_for_workspace(test_command, workspace)
    if test_targets:
        normalized = normalized + " " + " ".join(shlex.quote(target) for target in test_targets)
    try:
        result = subprocess.run(
            normalized,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=120,
            env=workspace_command_env(workspace),
        )
    except subprocess.TimeoutExpired:
        return False, "TimeoutExpired"
    except Exception as exc:
        return False, str(exc)

    if result.returncode == 0:
        return True, ""
    message = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
    first_lines = message.splitlines()
    return False, "\n".join(first_lines[:12]).strip()
