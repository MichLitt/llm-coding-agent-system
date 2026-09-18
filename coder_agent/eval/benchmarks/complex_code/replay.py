"""Clean-workspace buggy/gold replay for ComplexCodeBench fixtures."""
from __future__ import annotations
import shutil, subprocess, sys
from dataclasses import dataclass
from pathlib import Path
from coder_agent.eval.complex_models import ComplexCodeTaskSpec

@dataclass(frozen=True)
class ReplayResult:
    task_id: str
    buggy_failed: bool
    gold_passed: bool

def replay_task(task: ComplexCodeTaskSpec, root: Path, workspace: Path) -> ReplayResult:
    if workspace.exists(): shutil.rmtree(workspace)
    shutil.copytree(root / "setup_files", workspace)
    command = task.verification[0]["cmd"].split()
    buggy = subprocess.run([sys.executable, "-m", *command], cwd=workspace, capture_output=True, text=True)
    for source in (root / "gold").rglob("*"):
        if source.is_file():
            target = workspace / source.relative_to(root / "gold"); target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    gold = subprocess.run([sys.executable, "-m", *command], cwd=workspace, capture_output=True, text=True)
    return ReplayResult(task.task_id, buggy.returncode != 0, gold.returncode == 0)

def audit_clean_replays(tasks: list[ComplexCodeTaskSpec], root: Path, workspace_root: Path, repeats: int = 3) -> dict[str, list[ReplayResult]]:
    """Run every fixture from scratch repeatedly; reject incomplete evidence."""
    if repeats < 1: raise ValueError("repeats must be positive")
    results = {task.task_id: [replay_task(task, root, workspace_root / task.task_id / str(index)) for index in range(repeats)] for task in tasks}
    if not all(item.buggy_failed and item.gold_passed for entries in results.values() for item in entries):
        raise RuntimeError("ComplexCodeBench clean replay audit failed")
    return results
