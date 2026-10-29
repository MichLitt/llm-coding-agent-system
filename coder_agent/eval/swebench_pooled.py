"""Compare two configurations run with several seeds, pooled per instance.

Each configuration ran the same instances once per seed. An instance's score
is how many of its runs resolved it (0 to the number of seeds), and the
statistic is the summed score difference, candidate minus baseline.
Instances, not runs, are the independent units: an exact sign-flip test asks
whether the differences are symmetric around zero, and a bootstrap over
instances gives the 95% interval of the pooled resolved-rate difference.

Reads ``per_instance`` from a ``coder_agent.eval.swebench_summary`` output:

    uv run python -m coder_agent.eval.swebench_pooled --summary summary.json \\
        --baseline A-s101,A-s202 --candidate C-s101,C-s202
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from coder_agent.eval.swebench_summary import BOOTSTRAP_RESAMPLES


def sign_flip_p(differences: list[int]) -> float:
    """Exact two-sided p of the summed differences when each one's sign is a fair coin."""
    null = Counter({0: 1})
    for difference in differences:
        if difference:
            step: Counter[int] = Counter()
            for total, ways in null.items():
                step[total + difference] += ways
                step[total - difference] += ways
            null = step
    observed = abs(sum(differences))
    return sum(ways for total, ways in null.items() if abs(total) >= observed) / sum(null.values())


def pooled_bootstrap_interval(differences: list[int], runs: int, *, seed: int = 0) -> tuple[float, float]:
    """95% interval of the pooled resolved-rate difference, resampling instances."""
    n = len(differences)
    if n == 0:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(
        sum(differences[rng.randrange(n)] for _ in range(n)) / (n * runs) for _ in range(BOOTSTRAP_RESAMPLES)
    )
    return (means[int(0.025 * BOOTSTRAP_RESAMPLES)], means[int(0.975 * BOOTSTRAP_RESAMPLES) - 1])


def pooled_comparison(
    per_instance: dict[str, dict[str, str]], baseline: list[str], candidate: list[str]
) -> dict[str, Any]:
    if not baseline or len(baseline) != len(candidate):
        raise ValueError("baseline and candidate need the same number of arms, one per seed")
    ids = sorted(per_instance)
    missing = sorted({arm for i in ids for arm in [*baseline, *candidate] if arm not in per_instance[i]})
    if missing:
        raise ValueError(f"arms missing from per_instance: {missing}")
    runs = len(baseline)
    base = {i: sum(per_instance[i][arm] == "resolved" for arm in baseline) for i in ids}
    cand = {i: sum(per_instance[i][arm] == "resolved" for arm in candidate) for i in ids}
    differences = [cand[i] - base[i] for i in ids]
    low, high = pooled_bootstrap_interval(differences, runs)
    return {
        "baseline": list(baseline),
        "candidate": list(candidate),
        "n_instances": len(ids),
        "runs_per_instance": runs,
        "baseline_resolved_runs": sum(base.values()),
        "candidate_resolved_runs": sum(cand.values()),
        "rate_difference": round(sum(differences) / (len(ids) * runs), 4) if ids else None,
        "rate_difference_bootstrap95": [round(low, 4), round(high, 4)],
        "sign_flip_p": round(sign_flip_p(differences), 4),
        "score_differences": {str(k): v for k, v in sorted(Counter(differences).items())},
        # Where the candidate's resolves come from, by how often the baseline resolved each instance.
        "by_baseline_score": {
            str(score): {
                "instances": sum(base[i] == score for i in ids),
                "candidate_resolved_runs": sum(cand[i] for i in ids if base[i] == score),
            }
            for score in range(runs + 1)
        },
        "resolved_in_every_run": {
            "baseline": sum(score == runs for score in base.values()),
            "candidate": sum(score == runs for score in cand.values()),
        },
        "outcomes": {
            arm: dict(sorted(Counter(per_instance[i][arm] for i in ids).items())) for arm in [*baseline, *candidate]
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--summary", type=Path, required=True,
                        help="a swebench_summary output, or any JSON object with its per_instance map")
    parser.add_argument("--baseline", required=True, help="comma-separated arm names, one per seed")
    parser.add_argument("--candidate", required=True, help="comma-separated arm names, in the same seed order")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    per_instance = json.loads(args.summary.read_text(encoding="utf-8"))["per_instance"]
    try:
        result = pooled_comparison(per_instance, args.baseline.split(","), args.candidate.split(","))
    except ValueError as exc:
        parser.error(str(exc))
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
