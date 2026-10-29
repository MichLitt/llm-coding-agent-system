"""Read mini-SWE-agent output for WP1 arm R and retry its provider failures.

mini-SWE-agent writes ``<id>/<id>.traj.json`` per instance and a ``preds.json``
keyed by instance id. A run's exit status is ``Submitted``, a limit such as
``LimitsExceeded``, or the class name of the exception that ended it.
Pre-registration Amendment 2: provider failures (``RateLimitError`` and
similar) are retried once with identical settings into ``retry-1/``, and the
last attempt counts.

    uv run python -m coder_agent.eval.mini_swe_agent retry-ids --run-dir runs/wp1/formal/R
    uv run python -m coder_agent.eval.mini_swe_agent merge-predictions --run-dir runs/wp1/formal/R \\
        --label wp1-formal-R --output runs/wp1/formal/R/predictions.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

RETRY_DIR = "retry-1"
# litellm exception classes for a provider that refused or failed the request.
PROVIDER_EXIT_STATUSES = frozenset({
    "APIConnectionError",
    "APIError",
    "BadGatewayError",
    "InternalServerError",
    "RateLimitError",
    "ServiceUnavailableError",
    "Timeout",
})


def _record(path: Path, attempt: int) -> dict[str, Any]:
    trajectory = json.loads(path.read_text(encoding="utf-8"))
    info = trajectory.get("info") or {}
    replies = [m for m in trajectory.get("messages") or [] if m.get("role") == "assistant"]
    usages = [u for m in replies if (u := ((m.get("extra") or {}).get("response") or {}).get("usage"))]
    calls = (info.get("model_stats") or {}).get("api_calls")
    calls = len(replies) if calls is None else calls

    def total(read) -> int | None:
        return sum(read(u) or 0 for u in usages) if usages else None

    return {
        "instance_id": path.parent.name,
        "attempt": attempt,
        "termination_reason": info.get("exit_status"),
        "provider_error": info.get("exit_status") in PROVIDER_EXIT_STATUSES,
        "steps": len(replies),
        "tool_calls": sum(len(m.get("tool_calls") or []) for m in replies),
        "agent_version": info.get("mini_version"),
        "model_name": ((info.get("config") or {}).get("model") or {}).get("model_name"),
        "usage": {
            "llm_calls": calls,
            "input_tokens": total(lambda u: u.get("prompt_tokens")),
            "output_tokens": total(lambda u: u.get("completion_tokens")),
            "cached_input_tokens": total(lambda u: (u.get("prompt_tokens_details") or {}).get("cached_tokens")),
            # A failed call leaves no reply, so its usage was never reported.
            "token_usage_status": "provider" if len(usages) == calls else "partial",
        },
    }


def _records(directory: Path, attempt: int) -> list[dict[str, Any]]:
    return [_record(path, attempt) for path in sorted(directory.glob("*/*.traj.json"))]


def trajectory_records(run_dir: Path) -> list[dict[str, Any]]:
    """One record per attempt, the first attempt's records first."""
    return _records(run_dir, 1) + _records(run_dir / RETRY_DIR, 2)


def retry_ids(run_dir: Path) -> list[str]:
    """Instances whose first attempt ended in a provider failure."""
    return [record["instance_id"] for record in _records(run_dir, 1) if record["provider_error"]]


def merge_predictions(run_dir: Path, label: str) -> list[dict[str, Any]]:
    """Harness predictions, one per instance, from each instance's last attempt."""
    preds: dict[str, Any] = {}
    for directory in (run_dir, run_dir / RETRY_DIR):
        if (directory / "preds.json").exists():
            preds.update(json.loads((directory / "preds.json").read_text(encoding="utf-8")))
    return [
        {"instance_id": instance_id, "model_name_or_path": label, "model_patch": preds[instance_id].get("model_patch") or ""}
        for instance_id in sorted(preds)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("retry-ids", help="print first-attempt provider failures, one id per line").add_argument(
        "--run-dir", type=Path, required=True)
    merge = commands.add_parser("merge-predictions", help="write the harness predictions file")
    merge.add_argument("--run-dir", type=Path, required=True)
    merge.add_argument("--label", required=True)
    merge.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "retry-ids":
        for instance_id in retry_ids(args.run_dir):
            print(instance_id)
    else:
        rows = merge_predictions(args.run_dir, args.label)
        args.output.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
