"""Freeze and validate a ConversationBench test split without model execution."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from coder_agent.eval.benchmarks.conversation.loader import load_conversation_tasks


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def build_freeze_manifest(task_dir: Path, *, frozen_at: str | None = None) -> dict:
    tasks_path, fixtures = task_dir / "tasks.yaml", task_dir / "fixtures"
    tasks = load_conversation_tasks(tasks_path)
    if any(task.split != "test" for task in tasks):
        raise ValueError("only test-split tasks may be frozen")
    return {
        "schema_version": "conversation-freeze/v1",
        "frozen_at": frozen_at or datetime.now(UTC).isoformat(),
        "task_ids": [task.conversation_id for task in tasks],
        "tasks_yaml_sha256": _hash_file(tasks_path),
        "fixture_tree_sha256": _hash_tree(fixtures),
    }


def validate_freeze_manifest(task_dir: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current = build_freeze_manifest(task_dir, frozen_at=manifest.get("frozen_at"))
    for field in ("schema_version", "task_ids", "tasks_yaml_sha256", "fixture_tree_sha256"):
        if manifest.get(field) != current[field]:
            raise ValueError(f"Conversation test freeze mismatch: {field}")
    return manifest
