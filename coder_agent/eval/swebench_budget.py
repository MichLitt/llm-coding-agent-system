"""Budget checkpoint for a pre-registered SWE-bench run (WP1 pre-registration §7).

Once the first instances of the first arm have finished, this projects the
whole run's token usage and fails if the projection exceeds the cap:

    uv run python -m coder_agent.eval.swebench_budget --run-dir runs/wp1/formal/A-s101 \\
        --instance-runs 300 --max-input-tokens 45000000 --max-output-tokens 1000000

The projection is the mean provider-reported usage per finished instance,
times the planned number of instance runs across all arms. Instances without
usage (infrastructure failures) are left out of the mean. Exit status: 0
within the cap, 1 over it (stop and re-evaluate), 2 too few instances
measured.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from coder_agent.eval.swebench_summary import _last_attempts, _read_jsonl


def project(run_dir: Path, instance_runs: int) -> dict[str, Any]:
    records = _last_attempts(_read_jsonl(run_dir / "results.jsonl")).values()
    usage = [r["usage"] for r in records if (r.get("usage") or {}).get("input_tokens") is not None]
    measured = len(usage)
    mean_input = sum(u["input_tokens"] for u in usage) / measured if measured else 0.0
    mean_output = sum(u.get("output_tokens") or 0 for u in usage) / measured if measured else 0.0
    return {
        "run_dir": str(run_dir),
        "instances_measured": measured,
        "partial_usage_instances": sum(1 for u in usage if u.get("token_usage_status") != "provider"),
        "mean_input_tokens": round(mean_input),
        "mean_output_tokens": round(mean_output),
        "instance_runs": instance_runs,
        "projected_input_tokens": round(mean_input * instance_runs),
        "projected_output_tokens": round(mean_output * instance_runs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--instance-runs", type=int, required=True, help="planned instance runs across all arms")
    parser.add_argument("--max-input-tokens", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    parser.add_argument("--min-measured", type=int, default=25)
    args = parser.parse_args(argv)

    projection = project(args.run_dir, args.instance_runs)
    over = (
        projection["projected_input_tokens"] > args.max_input_tokens
        or projection["projected_output_tokens"] > args.max_output_tokens
    )
    projection["caps"] = {"input_tokens": args.max_input_tokens, "output_tokens": args.max_output_tokens}
    projection["verdict"] = (
        "too_few_measured" if projection["instances_measured"] < args.min_measured
        else "over_cap" if over else "within_cap"
    )
    print(json.dumps(projection, indent=2))
    return {"within_cap": 0, "over_cap": 1, "too_few_measured": 2}[projection["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
