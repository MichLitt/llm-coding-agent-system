"""Create an unaudited SWE-bench Lite candidate snapshot.

This is deliberately a curation tool, not a runtime dependency.  It downloads
only the exact Hugging Face dataset revision recorded in the checked-in source,
keeps the task allowlist explicit, and writes the official task fields verbatim.
It never overwrites the accepted source or generated manifest: promotion needs
separate environment and replay evidence.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from datasets import load_dataset


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "coder_agent" / "eval" / "benchmarks" / "swebench" / "official_tasks.source.json"
CANDIDATE_PATH = ROOT / "coder_agent" / "eval" / "benchmarks" / "swebench" / "promotion_candidates.source.json"

# The accepted lane stays stable until candidates pass replay audits.
PROMOTED_TASK_IDS = (
    "pylint-dev__pylint-5859",
    "sympy__sympy-22005",
    "pytest-dev__pytest-7220",
    "sphinx-doc__sphinx-8273",
    "pylint-dev__pylint-7993",
    "sympy__sympy-21627",
    "pytest-dev__pytest-7373",
    "pallets__flask-4992",
)

# The selection is intentionally model-blind. Four distinct repositories add
# API/library diversity, but are only eligible for promotion after environment
# setup and clean buggy/gold replay checks have passed.
CANDIDATE_TASK_IDS = (
    "pylint-dev__pylint-6506",
    "mwaskom__seaborn-3407",
    "psf__requests-3362",
    "pydata__xarray-5131",
)
OFFICIAL_FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "environment_setup_commit",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "test_patch",
    "patch",
)


def _read_source(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _official_task(record: dict[str, Any]) -> dict[str, Any]:
    task = {field: record[field] for field in OFFICIAL_FIELDS}
    for field in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        value = task[field]
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise ValueError(f"official {field} must decode to a list")
            task[field] = parsed
    return task


def build_candidate_snapshot(
    *,
    base_source_path: Path = SOURCE_PATH,
    output_path: Path = CANDIDATE_PATH,
    exported_at: str | None = None,
) -> dict[str, Any]:
    source = _read_source(base_source_path)
    revision = str(source["source_revision"])
    dataset_name = str(source["dataset_name"])
    dataset = load_dataset(dataset_name, split="test", revision=revision)
    by_id = {str(record["instance_id"]): record for record in dataset}
    selected_ids = CANDIDATE_TASK_IDS
    missing = [task_id for task_id in selected_ids if task_id not in by_id]
    if missing:
        raise ValueError(f"Pinned dataset revision is missing selected task(s): {', '.join(missing)}")

    refreshed = {
        **source,
        "exported_at": exported_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tasks": [_official_task(by_id[task_id]) for task_id in selected_ids],
    }
    output_path.write_text(json.dumps(refreshed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return refreshed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-source-path", type=Path, default=SOURCE_PATH)
    parser.add_argument("--output-path", type=Path, default=CANDIDATE_PATH)
    parser.add_argument("--exported-at", help="RFC3339 timestamp for deterministic regeneration")
    args = parser.parse_args()
    refreshed = build_candidate_snapshot(
        base_source_path=args.base_source_path,
        output_path=args.output_path,
        exported_at=args.exported_at,
    )
    print(f"Wrote {len(refreshed['tasks'])} SWE-bench Lite promotion candidates from {refreshed['source_revision']}")


if __name__ == "__main__":
    main()
