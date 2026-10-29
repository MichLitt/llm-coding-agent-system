"""EvalOps ``agent/v1`` aggregate reports for SWE-bench arms (WP1 pre-registration §8).

One report per arm, so EvalOps can compare arms on the same task set:

    uv run python -m coder_agent.eval.swebench_evalops --summary runs/wp1/formal/summary.json \\
        --arm A-s101=runs/wp1/formal/A-s101 --arm A-s202=runs/wp1/formal/A-s202 \\
        --arm R=runs/wp1/formal/R --output-dir runs/wp1/formal/evalops

The resolved rate, the task ids and token usage come from the summarizer's
``summary.json``, so the reports agree with the archived evidence. Steps, tool
calls and wall time come from each arm's run directory: ``results.jsonl`` and
``run_state.db`` for this Agent, trajectories for mini-SWE-agent. Every value
is a sum over each instance's last attempt. mini-SWE-agent does not time its
runs and its command exit codes do not mean what this Agent's tool errors
mean, so its wall time and tool success rate are null.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from coder_agent.eval.mini_swe_agent import trajectory_records
from coder_agent.eval.swebench_summary import _last_attempts, _read_jsonl
from coder_agent.evalops.schema import AgentRunReport

BENCHMARK_NAME = "swebench_verified_wp1_formal"


def _percentile(values: list[int], q: float) -> int | None:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None


def _tool_totals(run_dir: Path, run_ids: list[str]) -> tuple[int, float | None]:
    """Tool calls and their success rate from the Agent's run-state store."""
    connection = sqlite3.connect(f"file:{run_dir / 'run_state.db'}?mode=ro", uri=True)
    try:
        rows = [
            connection.execute("SELECT total_tool_calls, tool_success_rate FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            for run_id in run_ids
        ]
    finally:
        connection.close()
    if any(row is None for row in rows):
        raise SystemExit(f"{run_dir / 'run_state.db'} is missing runs listed in results.jsonl")
    calls = sum(row[0] for row in rows)
    successes = sum(row[0] * row[1] for row in rows if row[1] is not None)
    return calls, (successes / calls if calls else None)


def _token_usage_status(counts: dict[str, int]) -> str:
    if counts and set(counts) == {"provider"}:
        return "provider"
    return "partial" if counts.get("provider") or counts.get("partial") else "unavailable"


def build_report(name: str, run_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    arm = next((a for a in summary["arms"] if a["arm"] == name), None)
    if arm is None:
        raise SystemExit(f"arm {name!r} is not in the summary")
    task_ids = sorted(summary["per_instance"])
    usage = arm["usage"]
    tokens = (usage["input_tokens"] + usage["output_tokens"]
              if usage["input_tokens"] is not None and usage["output_tokens"] is not None else None)
    manifest = arm["provenance"]["manifest"]

    raw = _read_jsonl(run_dir / "results.jsonl")
    if raw:  # this Agent
        final = [r for i, r in _last_attempts(raw).items() if i in summary["per_instance"]]
        tool_calls, tool_success_rate = _tool_totals(run_dir, [r["run_id"] for r in final])
        durations = [round(r["duration_seconds"] * 1000) for r in final]
        identity = {"preset": manifest["preset"], "llm_profile": manifest["llm_profile"],
                    "git_commit": manifest["agent_commit"]}
    else:  # mini-SWE-agent
        final = [r for i, r in _last_attempts(trajectory_records(run_dir)).items() if i in summary["per_instance"]]
        tool_calls, tool_success_rate = sum(r["tool_calls"] for r in final), None
        durations = []
        identity = {"preset": f"mini-swe-agent-{final[0]['agent_version']}", "llm_profile": final[0]["model_name"],
                    "git_commit": None}
    if len(final) != len(task_ids):
        raise SystemExit(f"{run_dir} has records for {len(final)} of {len(task_ids)} instances")

    report = AgentRunReport(
        run_id=f"wp1-formal-{name}-aggregate-v1",
        run_type="eval",
        status="completed",
        total_steps=sum(r["steps"] for r in final),
        total_tool_calls=tool_calls,
        tool_success_rate=tool_success_rate,
        total_tokens=tokens,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        llm_call_count=usage["llm_calls"],
        token_usage_status=_token_usage_status(usage["token_usage_status"]),
        estimated_cost_usd=usage["estimated_cost_usd"],
        wall_duration_ms=sum(durations) if durations else None,
        benchmark_name=BENCHMARK_NAME,
        task_ids=task_ids,
        resolved_rate=arm["resolved_rate"],
        **identity,
    )
    return {
        **dataclasses.asdict(report),
        "wall_latency_p50_ms": _percentile(durations, 0.5),
        "wall_latency_p95_ms": _percentile(durations, 0.95),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--arm", action="append", required=True, metavar="NAME=RUN_DIR")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for spec in args.arm:
        name, sep, run_dir = spec.partition("=")
        if not sep or not name or not run_dir:
            parser.error(f"--arm must be NAME=RUN_DIR, got {spec!r}")
        report = build_report(name, Path(run_dir), summary)
        path = args.output_dir / f"{name}.agent-v1.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{path}: resolved_rate {report['resolved_rate']}, {report['total_steps']} steps, "
              f"{report['total_tool_calls']} tool calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
