from pathlib import Path
import yaml
from coder_agent.eval.complex_models import ComplexCodeTaskSpec


def load_complex_code_tasks(path: Path) -> list[ComplexCodeTaskSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("tasks", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list): raise ValueError("complex task manifest must contain a tasks list")
    tasks = [ComplexCodeTaskSpec.from_dict(item) for item in items]
    if len({task.task_id for task in tasks}) != len(tasks): raise ValueError("complex task IDs must be unique")
    return tasks
