#!/usr/bin/env bash
# Development check for the consecutive retry policy. Exploratory: not a
# pre-registered result.
#
# Reruns arm A on the WP1 formal sample with retry_policy=consecutive and
# otherwise the formal run's settings, grades it, and summarizes it with the
# formal arms A-s101, A-s202 and R. The change was chosen from this sample's
# results, so this check only decides whether a pre-registered held-out run
# is worth its cost.
#
# Run on the Linux x86_64 host from llm-coding-agent-system/, after scripts/wp1_formal.sh:
#
#   scripts/wp1_dev_retry_policy.sh
#
# Each seed takes about one GLM 5-hour quota window. SEEDS=101 runs one seed;
# re-running resumes. Overrides: SEEDS, OUT, FORMAL, WORKERS, HARNESS_PY,
# HARNESS_WORKERS.
set -euo pipefail
source "$(dirname "$0")/wp1_lib.sh"

FORMAL="${FORMAL:-runs/wp1/formal}"
OUT="${OUT:-runs/wp1/dev-consecutive}"
SEEDS="${SEEDS:-101 202}"
WORKERS="${WORKERS:-2}"
ORDER_SEED=20261029  # the formal run's processing order

for arm in A-s101 A-s202 R; do
  [ -f "$FORMAL/$arm/harness_report.json" ] || { echo "missing $FORMAL/$arm: run scripts/wp1_formal.sh first" >&2; exit 1; }
done
wp1_preflight
mkdir -p "$OUT"
arms=(--arm "A-s101=$FORMAL/A-s101" --arm "A-s202=$FORMAL/A-s202" --arm "R=$FORMAL/R")
for seed in $SEEDS; do
  uv run --frozen python -m coder_agent.eval.swebench_docker --instances "$FORMAL/formal.jsonl" \
    --output "$OUT/C-s$seed" --label "wp1-dev-C-s$seed" --preset C3 --llm-profile glm_5_nothink \
    --experiment-config "{\"policy_mode\":\"legacy\",\"model_seed\":$seed,\"retry_policy\":\"consecutive\"}" \
    --max-steps 40 --order-seed "$ORDER_SEED" --workers "$WORKERS" --retry-errors
  wp1_grade "$OUT/C-s$seed" "wp1-dev-C-s$seed"
  arms+=(--arm "C-s$seed=$OUT/C-s$seed")
done

uv run --frozen python -m coder_agent.eval.swebench_summary --instances "$FORMAL/formal.jsonl" "${arms[@]}" \
  --output "$OUT/summary.json" --markdown "$OUT/summary.md"
