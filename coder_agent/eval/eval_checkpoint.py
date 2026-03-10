import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from coder_agent.eval.metrics import EvalResult


def result_paths(output_dir: Path, config_label: str) -> tuple[Path, Path, Path]:
    stem = config_label or "results"
    return (
        output_dir / f"{stem}.json",
        output_dir / f"{stem}.jsonl",
        output_dir / f"{stem}_run_manifest.json",
    )


def clear_run_artifacts(output_dir: Path, config_label: str) -> None:
    for path in result_paths(output_dir, config_label):
        if path.exists():
            path.unlink()


def load_checkpoint_results(output_dir: Path, config_label: str) -> list[EvalResult]:
    _, checkpoint_path, _ = result_paths(output_dir, config_label)
    if not checkpoint_path.exists():
        return []

    results: list[EvalResult] = []
    index_by_task_id: dict[str, int] = {}
    with checkpoint_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            result = EvalResult(**raw)
            existing_index = index_by_task_id.get(result.task_id)
            if existing_index is None:
                index_by_task_id[result.task_id] = len(results)
                results.append(result)
            else:
                results[existing_index] = result
    return results


def append_checkpoint_result(output_dir: Path, config_label: str, result: EvalResult) -> None:
    _, checkpoint_path, _ = result_paths(output_dir, config_label)
    with checkpoint_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


def write_results_json(output_dir: Path, config_label: str, results: list[EvalResult]) -> None:
    out_path, _, _ = result_paths(output_dir, config_label)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump([asdict(result) for result in results], handle, indent=2, ensure_ascii=False)


def read_manifest(output_dir: Path, config_label: str) -> dict[str, Any]:
    _, _, manifest_path = result_paths(output_dir, config_label)
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def write_run_manifest(
    output_dir: Path,
    config_label: str,
    *,
    benchmark_name: str,
    preset: str,
    total_tasks: int,
    results: list[EvalResult],
    resume_enabled: bool,
    started_at: float,
    finished_at: float | None,
) -> None:
    _, _, manifest_path = result_paths(output_dir, config_label)
    manifest = {
        "config_label": config_label or "results",
        "benchmark": benchmark_name,
        "preset": preset,
        "git_commit": git_commit(),
        "started_at": started_at,
        "finished_at": finished_at,
        "completed_task_ids": [result.task_id for result in results],
        "total_tasks": total_tasks,
        "resume_enabled": resume_enabled,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"
