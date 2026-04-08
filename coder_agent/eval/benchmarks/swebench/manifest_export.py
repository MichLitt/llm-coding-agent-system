"""Generate the checked-in SWE-bench official manifest from a source snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).parent
DEFAULT_SOURCE_PATH = _ROOT / "official_tasks.source.json"
DEFAULT_OUTPUT_PATH = _ROOT / "official_manifest.generated.json"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_fields(
    data: dict[str, Any],
    fields: list[str],
    label: str,
    *,
    allow_empty: set[str] | None = None,
) -> None:
    allowed = allow_empty or set()
    missing = [
        field
        for field in fields
        if field not in data or (field not in allowed and data[field] in ("", None))
    ]
    if missing:
        raise ValueError(f"{label} missing required field(s): {', '.join(missing)}")


def export_official_manifest(
    source_path: Path = DEFAULT_SOURCE_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> dict[str, Any]:
    raw = _read_json(source_path)
    _require_fields(
        raw,
        [
            "dataset_name",
            "dataset_version",
            "source_source",
            "source_revision",
            "exported_at",
            "generator_version",
            "tasks",
        ],
        "swebench source snapshot",
    )
    tasks: list[dict[str, Any]] = []
    seen_instance_ids: set[str] = set()
    for item in raw["tasks"]:
        if not isinstance(item, dict):
            raise ValueError("swebench source snapshot tasks must be objects")
        _require_fields(
            item,
            [
                "instance_id",
                "repo",
                "base_commit",
                "problem_statement",
                "FAIL_TO_PASS",
                "PASS_TO_PASS",
                "test_patch",
                "environment_setup_commit",
            ],
            "swebench source task",
            allow_empty={"test_patch"},
        )
        instance_id = str(item["instance_id"])
        if instance_id in seen_instance_ids:
            raise ValueError(f"Duplicate swebench instance_id: {instance_id}")
        seen_instance_ids.add(instance_id)
        repo = str(item["repo"]).strip()
        tasks.append(
            {
                "task_id": instance_id,
                "instance_id": instance_id,
                "repo": repo,
                "repo_url": f"https://github.com/{repo}.git",
                "base_commit": str(item["base_commit"]).strip(),
                "problem_statement": str(item["problem_statement"]).strip(),
                "environment_setup_commit": str(item["environment_setup_commit"]).strip(),
                "fail_to_pass": [str(value) for value in item["FAIL_TO_PASS"]],
                "pass_to_pass": [str(value) for value in item["PASS_TO_PASS"]],
                "test_patch": str(item["test_patch"]),
            }
        )

    manifest = {
        "dataset_name": raw["dataset_name"],
        "dataset_version": raw["dataset_version"],
        "manifest_version": 1,
        "source_mode": "official_lite_generated_v1",
        "source_source": raw["source_source"],
        "source_revision": raw["source_revision"],
        "generated_at": raw["exported_at"],
        "generator_version": raw["generator_version"],
        "tasks": tasks,
    }
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    export_official_manifest()


if __name__ == "__main__":
    main()
