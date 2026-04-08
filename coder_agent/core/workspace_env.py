"""Workspace-local runtime environment helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


_SWEBENCH_VENV_DIRNAME = ".swebench-venv"


def workspace_python_executable(workspace: Path) -> Path:
    workspace = Path(workspace).resolve()
    if sys.platform.startswith("win"):
        candidate = workspace / _SWEBENCH_VENV_DIRNAME / "Scripts" / "python.exe"
    else:
        candidate = workspace / _SWEBENCH_VENV_DIRNAME / "bin" / "python"
    if candidate.exists():
        return candidate
    return Path(sys.executable)


def workspace_command_env(workspace: Path) -> dict[str, str]:
    workspace = Path(workspace).resolve()
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = str(workspace / ".uv-cache")
    python_exe = workspace_python_executable(workspace)
    venv_root = python_exe.parent.parent
    if python_exe != Path(sys.executable):
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.pop("__PYVENV_LAUNCHER__", None)
        env["VIRTUAL_ENV"] = str(venv_root)
        env["PATH"] = str(python_exe.parent) + os.pathsep + env.get("PATH", "")
        env["PYTHONNOUSERSITE"] = "1"
    return env
