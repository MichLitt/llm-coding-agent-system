"""Summarize SWE-bench arms from their raw artifacts (WP1 pre-registration §5).

One command recomputes the whole report from the run directories:

    uv run python -m coder_agent.eval.swebench_summary --instances instances.jsonl \\
        --arm A-s101=runs/A-s101 --arm A-s202=runs/A-s202 --arm R=runs/R \\
        --output wp1-summary.json --markdown wp1-summary.md

Each arm directory holds the official harness report as ``harness_report.json``.
Arms produced by ``coder_agent.eval.swebench_docker`` also hold
``manifest.json``, ``results.jsonl`` and ``predictions.jsonl``. An external arm
(for example mini-SWE-agent) may hold only the harness report.

Every instance in ``--instances`` gets exactly one outcome per arm. The
resolved rate is over all instances; infrastructure, provider and harness
failures are listed by id, never dropped. Pairs of arms are compared per
instance with an exact McNemar test and a paired bootstrap interval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from coder_agent.eval.swebench_docker import is_provider_failure

SCHEMA_VERSION = "swebench-summary/v1"
OUTCOMES = (
    "resolved",
    "unresolved",
    "empty_patch",
    "wall_cap",
    "infra_error",
    "provider_error",
    "harness_error",
    "not_graded",
)
BOOTSTRAP_RESAMPLES = 10_000


def wilson_interval(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def exact_mcnemar_p(only_first: int, only_second: int) -> float:
    n = only_first + only_second
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(only_first, only_second) + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_bootstrap_interval(first: list[bool], second: list[bool], *, seed: int = 0) -> tuple[float, float]:
    """95% interval of resolved-rate(first) - resolved-rate(second), resampling instances."""
    n = len(first)
    if n == 0:
        return (0.0, 0.0)
    diffs = [int(a) - int(b) for a, b in zip(first, second)]
    rng = random.Random(seed)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOTSTRAP_RESAMPLES))
    return (means[int(0.025 * BOOTSTRAP_RESAMPLES)], means[int(0.975 * BOOTSTRAP_RESAMPLES) - 1])


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _last_attempts(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        latest[record["instance_id"]] = record
    return latest


def _outcome(instance_id: str, record: dict[str, Any] | None, report: dict[str, Any]) -> str:
    if record is not None:
        if record.get("error"):
            return "infra_error"
        if is_provider_failure(record):
            return "provider_error"
        if record.get("budget_failure") == "wall_cap":
            return "wall_cap"
    for outcome, key in (
        ("resolved", "resolved_ids"),
        ("unresolved", "unresolved_ids"),
        ("empty_patch", "empty_patch_ids"),
        ("harness_error", "error_ids"),
        ("harness_error", "infra_failure_ids"),
    ):
        if instance_id in set(report.get(key) or []):
            return outcome
    return "not_graded"


def _distribution(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "mean": round(statistics.fmean(ordered), 1),
        "median": round(statistics.median(ordered), 1),
        "p90": round(ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)], 1),
        "max": round(ordered[-1], 1),
    }


def summarize_arm(name: str, run_dir: Path, instance_ids: list[str], instances_sha256: str) -> dict[str, Any]:
    report_path = run_dir / "harness_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    raw_records = _read_jsonl(run_dir / "results.jsonl")
    records = _last_attempts(raw_records)

    outcomes = {instance_id: _outcome(instance_id, records.get(instance_id), report) for instance_id in instance_ids}
    counts = Counter(outcomes.values())
    resolved = counts["resolved"]
    low, high = wilson_interval(resolved, len(instance_ids))

    final = [records[i] for i in instance_ids if i in records]
    usage = [record.get("usage") or {} for record in final]

    def usage_sum(key: str) -> int | None:
        values = [u.get(key) for u in usage if u.get(key) is not None]
        return sum(values) if values else None

    warnings = []
    if manifest is not None and manifest.get("instances_file_sha256") != instances_sha256:
        warnings.append("manifest instances_file_sha256 differs from --instances")
    if not report:
        warnings.append("harness_report.json missing: nothing is graded")
    image_ids = {(r.get("image") or {}).get("image_id") for r in final} - {None}

    return {
        "arm": name,
        "run_dir": str(run_dir),
        "n": len(instance_ids),
        "resolved": resolved,
        "resolved_rate": round(resolved / len(instance_ids), 4) if instance_ids else None,
        "resolved_rate_wilson95": [round(low, 4), round(high, 4)],
        "outcome_counts": {outcome: counts[outcome] for outcome in OUTCOMES},
        "failure_ids": {
            outcome: sorted(i for i, o in outcomes.items() if o == outcome)
            for outcome in ("infra_error", "provider_error", "harness_error", "wall_cap", "not_graded")
        },
        "retried_instances": sorted({r["instance_id"] for r in raw_records if r.get("attempt", 1) > 1}),
        "termination_reasons": dict(Counter(r.get("termination_reason") for r in final if r.get("termination_reason"))),
        "usage": {
            "token_usage_status": dict(Counter(u.get("token_usage_status") for u in usage if u)),
            "llm_calls": usage_sum("llm_calls"),
            "input_tokens": usage_sum("input_tokens"),
            "output_tokens": usage_sum("output_tokens"),
            "cached_input_tokens": usage_sum("cached_input_tokens"),
            "estimated_cost_usd": usage_sum("estimated_cost_usd"),
        },
        "steps": _distribution([r["steps"] for r in final if r.get("steps") is not None]),
        "duration_seconds": _distribution([r["duration_seconds"] for r in final if r.get("duration_seconds") is not None]),
        "provenance": {
            "manifest": {
                key: manifest.get(key)
                for key in (
                    "label", "agent_commit", "preset", "llm_profile", "experiment_config", "model_settings",
                    "max_steps", "wall_cap_seconds", "instances_file_sha256", "network", "cpus", "memory",
                )
            } if manifest else None,
            "harness_report_sha256": _sha256(report_path),
            "harness_schema_version": report.get("schema_version"),
            "predictions_sha256": _sha256(run_dir / "predictions.jsonl"),
            "distinct_image_ids": len(image_ids),
        },
        "warnings": warnings,
        "_outcomes": outcomes,
    }


def compare_arms(first: dict[str, Any], second: dict[str, Any], instance_ids: list[str]) -> dict[str, Any]:
    a = [first["_outcomes"][i] == "resolved" for i in instance_ids]
    b = [second["_outcomes"][i] == "resolved" for i in instance_ids]
    only_first = sum(x and not y for x, y in zip(a, b))
    only_second = sum(y and not x for x, y in zip(a, b))
    low, high = paired_bootstrap_interval(a, b)
    return {
        "first": first["arm"],
        "second": second["arm"],
        "both_resolved": sum(x and y for x, y in zip(a, b)),
        "only_first": only_first,
        "only_second": only_second,
        "neither": sum(not x and not y for x, y in zip(a, b)),
        "exact_mcnemar_p": round(exact_mcnemar_p(only_first, only_second), 4),
        "rate_difference": round((sum(a) - sum(b)) / len(instance_ids), 4) if instance_ids else None,
        "rate_difference_bootstrap95": [round(low, 4), round(high, 4)],
    }


def summarize(instances_path: Path, arms: list[tuple[str, Path]]) -> dict[str, Any]:
    instance_ids = [json.loads(line)["instance_id"] for line in instances_path.read_text().splitlines() if line.strip()]
    instances_sha256 = hashlib.sha256(instances_path.read_bytes()).hexdigest()
    arm_summaries = [summarize_arm(name, run_dir, instance_ids, instances_sha256) for name, run_dir in arms]
    pairs = [
        compare_arms(arm_summaries[i], arm_summaries[j], instance_ids)
        for i in range(len(arm_summaries)) for j in range(i + 1, len(arm_summaries))
    ]
    per_instance = {i: {arm["arm"]: arm["_outcomes"][i] for arm in arm_summaries} for i in instance_ids}
    for arm in arm_summaries:
        del arm["_outcomes"]
    return {
        "schema_version": SCHEMA_VERSION,
        "instances_file": str(instances_path),
        "instances_file_sha256": instances_sha256,
        "n": len(instance_ids),
        "arms": arm_summaries,
        "pairs": pairs,
        "per_instance": per_instance,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# SWE-bench summary ({summary['n']} instances)",
        "",
        f"Instances file sha256: `{summary['instances_file_sha256']}`. "
        "Recompute with `python -m coder_agent.eval.swebench_summary`.",
        "",
        "| Arm | Resolved | Rate (95% Wilson) | Infra | Provider | Harness | Wall cap | Output tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm in summary["arms"]:
        counts = arm["outcome_counts"]
        low, high = arm["resolved_rate_wilson95"]
        rate = arm["resolved_rate"] or 0.0
        output_tokens = arm["usage"]["output_tokens"]
        lines.append(
            f"| {arm['arm']} | {arm['resolved']}/{arm['n']} | {rate:.1%} ({low:.1%}–{high:.1%}) "
            f"| {counts['infra_error']} | {counts['provider_error']} | {counts['harness_error']} | {counts['wall_cap']} "
            f"| {output_tokens if output_tokens is not None else 'n/a'} |"
        )
    if summary["pairs"]:
        lines += [
            "",
            "| Pair | Both | Only first | Only second | Neither | Exact McNemar p | Rate diff (95% bootstrap) |",
            "|---|---|---|---|---|---|---|",
        ]
        for pair in summary["pairs"]:
            low, high = pair["rate_difference_bootstrap95"]
            lines.append(
                f"| {pair['first']} vs {pair['second']} | {pair['both_resolved']} | {pair['only_first']} "
                f"| {pair['only_second']} | {pair['neither']} | {pair['exact_mcnemar_p']} "
                f"| {pair['rate_difference']:+.1%} ({low:+.1%} to {high:+.1%}) |"
            )
    for arm in summary["arms"]:
        failures = {key: ids for key, ids in arm["failure_ids"].items() if ids}
        if failures or arm["warnings"]:
            lines += ["", f"**{arm['arm']}**"]
            lines += [f"- {key}: {', '.join(ids)}" for key, ids in failures.items()]
            lines += [f"- warning: {warning}" for warning in arm["warnings"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--arm", action="append", required=True, metavar="NAME=RUN_DIR")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)

    arms = []
    for spec in args.arm:
        name, sep, run_dir = spec.partition("=")
        if not sep or not name or not run_dir:
            parser.error(f"--arm must be NAME=RUN_DIR, got {spec!r}")
        arms.append((name, Path(run_dir)))
    summary = summarize(args.instances, arms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(render_markdown(summary), encoding="utf-8")
    print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
