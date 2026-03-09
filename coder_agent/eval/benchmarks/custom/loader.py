"""Loader for custom multi-step task YAML definitions."""

from pathlib import Path
from typing import Any

import yaml

from coder_agent.eval.runner import TaskSpec


_DEFAULT_TASKS_FILE = Path(__file__).parent / "tasks.yaml"


def load_custom_tasks(tasks_file: Path = _DEFAULT_TASKS_FILE) -> list[TaskSpec]:
    """Parse tasks.yaml and return a list of TaskSpec objects."""
    with tasks_file.open(encoding="utf-8") as f:
        raw: list[dict[str, Any]] = yaml.safe_load(f) or []

    tasks = []
    for d in raw:
        tasks.append(TaskSpec(
            task_id=d.get("task_id", d.get("name", "")),
            description=d.get("description", "").strip(),
            difficulty=d.get("difficulty", "medium"),
            setup_files=d.get("setup_files", []),
            verification=d.get("verification", []),
            verification_contract=d.get("verification_contract", {"mode": "custom_commands", "max_attempts": 2}),
            max_steps=d.get("max_steps", 15),
            metadata={
                "name": d.get("name", ""),
                "benchmark": "custom",
            },
        ))
    return tasks
