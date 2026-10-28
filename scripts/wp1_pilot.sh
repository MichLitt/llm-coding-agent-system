#!/usr/bin/env bash
# WP1 pilot (report/PREREG_WP1_SWEBENCH_VERIFIED_DRAFT.md): export the 10-instance
# pilot sample, run arm A (seed 101) inside the official SWE-bench images and
# arm R (mini-SWE-agent), grade both with the unmodified harness, and summarize.
#
# Run on the Linux x86_64 host from llm-coding-agent-system/, with the GLM key in .env:
#
#   scripts/wp1_pilot.sh
#
# Re-running resumes: finished instances are skipped and infrastructure or
# provider failures are retried once. Overrides: HARNESS_PY, OUT, WORKERS,
# HARNESS_WORKERS, SKIP_REFERENCE=1 (arm A only).
set -euo pipefail
source "$(dirname "$0")/wp1_lib.sh"

OUT="${OUT:-runs/wp1/pilot}"
WORKERS="${WORKERS:-2}"

wp1_preflight
mkdir -p "$OUT"
if [ ! -f "$OUT/pilot.jsonl" ]; then
  "$HARNESS_PY" scripts/export_swebench_instances.py --dataset "$DATASET" \
    --sample 10 --seed 20261027 --output "$OUT/pilot.jsonl"
  python3 -c "import json, sys; [print(json.loads(line)['instance_id']) for line in open(sys.argv[1])]" \
    "$OUT/pilot.jsonl" > "$OUT/pilot-ids.txt"
fi

uv run --frozen python -m coder_agent.eval.swebench_docker --instances "$OUT/pilot.jsonl" \
  --output "$OUT/A-s101" --label wp1-pilot-A-s101 --preset C3 --llm-profile glm_5_nothink \
  --experiment-config '{"policy_mode":"legacy","model_seed":101}' --max-steps 40 \
  --workers "$WORKERS" --retry-errors
wp1_grade "$OUT/A-s101" wp1-pilot-A-s101
arms=(--arm "A-s101=$OUT/A-s101")

if [ "${SKIP_REFERENCE:-0}" != 1 ]; then
  scripts/wp1_reference_arm.sh "$OUT/pilot.jsonl" "$OUT/R" wp1-pilot-R
  arms+=(--arm "R=$OUT/R")
fi

uv run --frozen python -m coder_agent.eval.swebench_summary --instances "$OUT/pilot.jsonl" \
  "${arms[@]}" --output "$OUT/summary.json" --markdown "$OUT/summary.md"
