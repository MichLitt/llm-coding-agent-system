import json
import hashlib
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


def _run_git_command(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return False, ""
    if result.returncode != 0:
        return False, ""
    return True, result.stdout.strip()


def _normalize_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_snapshot(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_snapshot(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _snapshot_sha256(value: Any) -> str:
    payload = json.dumps(_normalize_snapshot(value), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git_snapshot() -> dict[str, Any]:
    ok_full, full_commit = _run_git_command(["rev-parse", "HEAD"])
    ok_short, short_commit = _run_git_command(["rev-parse", "--short", "HEAD"])
    ok_status, status_porcelain = _run_git_command(["status", "--short"])
    ok_untracked, untracked = _run_git_command(["ls-files", "--others", "--exclude-standard"])
    ok_diff, tracked_diff = _run_git_command(["diff", "HEAD"])
    untracked_files = sorted(line for line in untracked.splitlines() if line) if ok_untracked else []
    status_text = status_porcelain if ok_status else ""
    diff_text = tracked_diff if ok_diff else ""
    return {
        "git_commit": short_commit if ok_short else "unknown",
        "git_commit_short": short_commit if ok_short else "unknown",
        "git_commit_full": full_commit if ok_full else "unknown",
        "git_is_dirty": bool(status_text),
        "git_status_porcelain": status_text,
        "git_diff_tracked_sha256": hashlib.sha256(diff_text.encode("utf-8")).hexdigest(),
        "git_untracked_files": untracked_files,
    }


def write_run_manifest(
    output_dir: Path,
    config_label: str,
    *,
    benchmark_name: str,
    preset: str,
    agent_config_snapshot: dict[str, Any] | None,
    runtime_experiment_config_snapshot: dict[str, Any] | None,
    total_tasks: int,
    results: list[EvalResult],
    resume_enabled: bool,
    started_at: float,
    finished_at: float | None,
    llm_profile_name: str | None = None,
    llm_model: str | None = None,
    llm_transport: str | None = None,
) -> None:
    _, _, manifest_path = result_paths(output_dir, config_label)
    normalized_agent_config = _normalize_snapshot(agent_config_snapshot or {})
    normalized_runtime_config = _normalize_snapshot(runtime_experiment_config_snapshot or {})
    combined_snapshot = {
        "benchmark": benchmark_name,
        "preset": preset,
        "agent_config": normalized_agent_config,
        "experiment_config": normalized_runtime_config,
    }
    manifest = {
        "config_label": config_label or "results",
        "benchmark": benchmark_name,
        "preset": preset,
        **_git_snapshot(),
        "started_at": started_at,
        "finished_at": finished_at,
        "completed_task_ids": [result.task_id for result in results],
        "total_tasks": total_tasks,
        "resume_enabled": resume_enabled,
        "llm_profile": llm_profile_name or "legacy",
        "llm_model": llm_model,
        "llm_transport": llm_transport,
        "agent_config_snapshot": normalized_agent_config,
        "agent_config_sha256": _snapshot_sha256(normalized_agent_config),
        "runtime_experiment_config_snapshot": normalized_runtime_config,
        "runtime_experiment_config_sha256": _snapshot_sha256(normalized_runtime_config),
        "experiment_config_snapshot": combined_snapshot,
        "experiment_config_sha256": _snapshot_sha256(combined_snapshot),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def git_commit() -> str:
    return _git_snapshot()["git_commit"]
