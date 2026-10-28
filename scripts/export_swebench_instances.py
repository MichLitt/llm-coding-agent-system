#!/usr/bin/env python3
"""Export a reproducible SWE-bench sample for ``coder_agent.eval.swebench_docker``.

Run it with the SWE-bench harness environment, which provides ``datasets``:

    ~/swebench-harness/.venv/bin/python scripts/export_swebench_instances.py \\
        --dataset SWE-bench/SWE-bench_Verified --sample 100 --seed 20261027 --output instances.jsonl

Only the fields the Agent may see (plus identifiers) are written; the hidden
tests stay in the harness. A sidecar ``.meta.json`` records the dataset,
fingerprint, seed and selected ids for pre-registration.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

PUBLIC_FIELDS = ("instance_id", "repo", "base_commit", "version", "problem_statement", "created_at")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="SWE-bench/SWE-bench_Verified")
    parser.add_argument("--split", default="test")
    parser.add_argument("--sample", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--exclude", type=Path, help="file of instance ids to exclude (e.g. a pilot set)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    from datasets import load_dataset

    dataset = load_dataset(args.dataset, split=args.split)
    excluded = set(args.exclude.read_text().split()) if args.exclude else set()
    pool = sorted(instance_id for instance_id in dataset["instance_id"] if instance_id not in excluded)
    chosen = sorted(random.Random(args.seed).sample(pool, args.sample))
    by_id = {row["instance_id"]: row for row in dataset}
    with args.output.open("w", encoding="utf-8") as handle:
        for instance_id in chosen:
            row = by_id[instance_id]
            handle.write(json.dumps({field: row.get(field) for field in PUBLIC_FIELDS}) + "\n")
    meta = {
        "dataset": args.dataset,
        "split": args.split,
        "dataset_fingerprint": getattr(dataset, "_fingerprint", None),
        "dataset_rows": len(dataset),
        "seed": args.seed,
        "sample": args.sample,
        "excluded": sorted(excluded),
        "instance_ids": chosen,
    }
    args.output.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(chosen)} instances to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
