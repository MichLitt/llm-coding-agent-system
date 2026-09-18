from __future__ import annotations

from pathlib import Path

import yaml

from coder_agent.eval.conversation_models import ConversationTaskSpec


def load_conversation_tasks(path: Path) -> list[ConversationTaskSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tasks = raw.get("tasks", raw) if isinstance(raw, dict) else raw
    if not isinstance(tasks, list):
        raise ValueError("conversation manifest must contain a tasks list")
    loaded = [ConversationTaskSpec.from_dict(item) for item in tasks]
    if len({task.conversation_id for task in loaded}) != len(loaded):
        raise ValueError("conversation IDs must be unique")
    return loaded
