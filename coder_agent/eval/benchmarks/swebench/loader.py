"""Loader for a fixed official SWE-bench Lite subset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from coder_agent.eval.runner import TaskSpec


_ROOT = Path(__file__).parent
_DEFAULT_OFFICIAL_MANIFEST_PATH = _ROOT / "official_manifest.generated.json"
_DEFAULT_OVERRIDES_PATH = _ROOT / "local_overrides.json"
_VALID_SUBSETS = {"smoke", "promoted"}
_DEFAULT_TEST_COMMAND = "python -m pytest -q"
_ALLOWED_OVERRIDE_FIELDS = {
    "instance_id",
    "subset",
    "mirror_path",
    "python_version",
    "setup_commands",
    "test_command_override",
    "expected_patch_targets",
    "expected_patch_target_count",
    "authorized_test_edit_paths",
    "setup_complexity",
    "primary_failure_mode_category",
    "max_steps",
}
_PROTECTED_OFFICIAL_FIELDS = {
    "task_id",
    "repo",
    "repo_url",
    "base_commit",
    "problem_statement",
    "fail_to_pass",
    "pass_to_pass",
    "test_patch",
    "environment_setup_commit",
}


def _manifest_sha256(manifest_path: Path) -> str:
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _require_fields(
    data: dict[str, Any],
    *,
    fields: list[str],
    label: str,
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


def _patch_paths(test_patch: str) -> list[str]:
    paths: list[str] = []
    for line in test_patch.splitlines():
        if not line.startswith("+++ b/"):
            continue
        path = line[len("+++ b/") :].strip()
        if not path or path == "/dev/null" or path in paths:
            continue
        paths.append(path)
    return paths


def _build_agent_prompt(task: dict[str, Any]) -> str:
    expected_targets = task.get("expected_patch_targets") or []
    target_hint = ""
    if expected_targets:
        target_hint = "Likely patch targets: " + ", ".join(expected_targets) + ".\n"
    regression_hint = ""
    verification_files = [str(item).strip() for item in task.get("verification_files", []) if str(item).strip()]
    if verification_files:
        regression_hint = (
            "Verification overlays official regression coverage from test_patch files: "
            + ", ".join(verification_files)
            + ". Update those files locally if the fix requires matching test changes.\n"
        )
    authorized_test_paths = [str(item).strip() for item in task.get("authorized_test_edit_paths", []) if str(item).strip()]
    authorized_test_hint = ""
    if authorized_test_paths:
        authorized_test_hint = (
            "Authorized regression test edit paths: "
            + ", ".join(authorized_test_paths)
            + ". Do not edit unrelated tests during verification recovery.\n"
        )

    return (
        "You are fixing a bug in a repository already checked out in the workspace.\n\n"
        f"Problem statement:\n{task['problem_statement'].strip()}\n\n"
        f"{target_hint}"
        f"{regression_hint}"
        f"{authorized_test_hint}"
        f"Validation command:\n`{task['test_command']}`\n\n"
        "Required workflow:\n"
        "1. Inspect the repository files in the workspace.\n"
        "2. Edit the implementation to fix the bug.\n"
        "3. Run the validation command and the listed target tests until they pass.\n"
        "4. When verification succeeds, stop and provide a short summary.\n"
    )


def _normalize_override_subsets(raw_subset: Any) -> list[str]:
    if isinstance(raw_subset, list):
        subsets = [str(item).strip() for item in raw_subset if str(item).strip()]
    else:
        subsets = [str(raw_subset).strip()]
    if not subsets:
        raise ValueError("swebench override subset must not be empty")
    invalid = [subset for subset in subsets if subset not in _VALID_SUBSETS]
    if invalid:
        raise ValueError(f"Unsupported swebench override subset: {', '.join(invalid)}")
    return subsets


def _load_overrides(overrides_path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    if not overrides_path.exists():
        return {}, _manifest_sha256(overrides_path) if overrides_path.exists() else ""

    raw = json.loads(overrides_path.read_text(encoding="utf-8"))
    _require_fields(raw, fields=["manifest_version", "overrides"], label="swebench overrides")
    if not isinstance(raw["overrides"], list):
        raise ValueError("swebench overrides field 'overrides' must be a list")

    overrides_by_id: dict[str, dict[str, Any]] = {}
    for item in raw["overrides"]:
        if not isinstance(item, dict):
            raise ValueError("Each swebench override entry must be an object")
        _require_fields(item, fields=["instance_id", "subset"], label="swebench override")
        instance_id = str(item["instance_id"]).strip()
        fields = set(item.keys())
        invalid = sorted(fields - _ALLOWED_OVERRIDE_FIELDS)
        protected = sorted(fields & _PROTECTED_OFFICIAL_FIELDS)
        if protected:
            raise ValueError(
                f"swebench override {instance_id} cannot override official field(s): {', '.join(protected)}"
            )
        if invalid:
            raise ValueError(
                f"swebench override {instance_id} contains unsupported field(s): {', '.join(invalid)}"
            )
        item["subset"] = _normalize_override_subsets(item["subset"])
        if instance_id in overrides_by_id:
            raise ValueError(f"Duplicate swebench override instance_id: {instance_id}")
        overrides_by_id[instance_id] = item
    return overrides_by_id, _manifest_sha256(overrides_path)


def load_swebench_tasks(
    subset: str = "smoke",
    official_manifest_path: Path = _DEFAULT_OFFICIAL_MANIFEST_PATH,
    overrides_path: Path = _DEFAULT_OVERRIDES_PATH,
) -> list[TaskSpec]:
    if subset not in _VALID_SUBSETS:
        raise ValueError(f"Unsupported swebench subset: {subset}")
    if not official_manifest_path.exists():
        raise FileNotFoundError(f"SWE-bench official manifest not found: {official_manifest_path}")

    raw = json.loads(official_manifest_path.read_text(encoding="utf-8"))
    _require_fields(
        raw,
        fields=[
            "dataset_name",
            "dataset_version",
            "manifest_version",
            "source_mode",
            "source_source",
            "source_revision",
            "generated_at",
            "generator_version",
            "tasks",
        ],
        label="swebench official manifest",
    )
    if not isinstance(raw["tasks"], list):
        raise ValueError("swebench official manifest field 'tasks' must be a list")

    official_manifest_hash = _manifest_sha256(official_manifest_path)
    overrides_by_id, overrides_hash = _load_overrides(overrides_path)
    tasks: list[TaskSpec] = []
    seen_task_ids: set[str] = set()
    for item in raw["tasks"]:
        if not isinstance(item, dict):
            raise ValueError("Each swebench task entry must be an object")
        _require_fields(
            item,
            fields=[
                "task_id",
                "instance_id",
                "repo",
                "repo_url",
                "base_commit",
                "problem_statement",
                "fail_to_pass",
                "pass_to_pass",
                "test_patch",
                "environment_setup_commit",
            ],
            label=f"swebench task {item.get('task_id', '<unknown>')}",
            allow_empty={"test_patch"},
        )
        task_id = str(item["task_id"]).strip()
        if task_id in seen_task_ids:
            raise ValueError(f"Duplicate swebench task_id: {task_id}")
        seen_task_ids.add(task_id)

        override = overrides_by_id.get(str(item["instance_id"]).strip())
        if override is None or subset not in override["subset"]:
            continue

        test_patch = str(item.get("test_patch", ""))
        verification_files = _patch_paths(test_patch)
        test_command = str(override.get("test_command_override") or _DEFAULT_TEST_COMMAND)
        expected_patch_targets = [str(path).strip() for path in override.get("expected_patch_targets", []) if str(path).strip()]
        authorized_test_edit_paths = [
            str(path).strip()
            for path in override.get("authorized_test_edit_paths", [])
            if str(path).strip()
        ]
        if not authorized_test_edit_paths and verification_files:
            authorized_test_edit_paths = list(verification_files)
        metadata = {
            "benchmark": "swebench",
            "instance_id": item["instance_id"],
            "repo": item["repo"],
            "subset": subset,
            "subset_membership": list(override["subset"]),
            "repo_url": item["repo_url"],
            "mirror_path": override.get("mirror_path"),
            "base_commit": item["base_commit"],
            "problem_statement": item["problem_statement"],
            "python_version": override.get("python_version"),
            "setup_commands": list(override.get("setup_commands", [])),
            "setup_complexity": str(override.get("setup_complexity") or ("low" if not override.get("setup_commands") else "medium")),
            "test_command": test_command,
            "fail_to_pass": list(item["fail_to_pass"]),
            "pass_to_pass": list(item["pass_to_pass"]),
            "test_patch": test_patch,
            "verification_files": verification_files,
            "expected_patch_targets": expected_patch_targets,
            "expected_patch_target_count": int(override.get("expected_patch_target_count") or len(expected_patch_targets)),
            "authorized_test_edit_paths": authorized_test_edit_paths,
            "primary_failure_mode_category": str(
                override.get("primary_failure_mode_category") or "genuine_implementation_miss"
            ),
            "dataset_name": raw["dataset_name"],
            "dataset_version": raw["dataset_version"],
            "manifest_version": raw["manifest_version"],
            "source_mode": raw["source_mode"],
            "source_source": raw["source_source"],
            "source_revision": raw["source_revision"],
            "generated_at": raw["generated_at"],
            "generator_version": raw["generator_version"],
            "environment_setup_commit": item["environment_setup_commit"],
            "official_manifest_path": str(official_manifest_path.resolve()),
            "official_manifest_sha256": official_manifest_hash,
            "overrides_manifest_path": str(overrides_path.resolve()),
            "overrides_manifest_sha256": overrides_hash,
        }
        tasks.append(
            TaskSpec(
                task_id=task_id,
                description=_build_agent_prompt(metadata),
                difficulty="medium",
                setup_files=[],
                verification=[],
                verification_contract={"mode": "swebench_patch_and_test", "max_attempts": 2},
                max_steps=int(override.get("max_steps", 15)),
                metadata=metadata,
            )
        )
    return tasks
