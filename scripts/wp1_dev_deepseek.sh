#!/usr/bin/env bash
# Development check on DeepSeek V4.1 Flash: does the consecutive retry policy
# also help on another model? Exploratory: not a pre-registered result.
#
# Runs arm A (retry_policy=cumulative) and the candidate (consecutive) on the
# WP1 formal sample with profile deepseek_flash_nothink and otherwise the
# formal run's settings, grades each arm and seed, and compares the two pooled
# per instance (coder_agent.eval.swebench_pooled). The GLM arms are left out:
# they ran another model.
#
# Run on the Linux x86_64 host from llm-coding-agent-system/, with
# LLM_DEEPSEEK_API_KEY in .env:
#
#   scripts/wp1_dev_deepseek.sh
#
# DeepSeek bills per token and has no 5-hour quota. SEEDS=101 runs one seed;
# re-running resumes, and provider and infrastructure failures are retried up
# to MAX_ATTEMPTS attempts in total. Overrides: SEEDS, OUT, FORMAL, WORKERS,
# MAX_ATTEMPTS, HARNESS_PY, HARNESS_WORKERS.
set -euo pipefail
source "$(dirname "$0")/wp1_lib.sh"

FORMAL="${FORMAL:-runs/wp1/formal}"
OUT="${OUT:-runs/wp1/dev-deepseek}"
SEEDS="${SEEDS:-101 202}"
WORKERS="${WORKERS:-4}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-8}"
PROFILE=deepseek_flash_nothink
ORDER_SEED=20261029  # the formal run's processing order

[ -f "$FORMAL/formal.jsonl" ] || { echo "missing $FORMAL/formal.jsonl: run scripts/wp1_formal.sh first" >&2; exit 1; }
wp1_dev_preflight "$PROFILE"
mkdir -p "$OUT"
for seed in $SEEDS; do
  for arm in A C; do
    if [ "$arm" = A ]; then policy=cumulative; else policy=consecutive; fi
    name="D$arm-s$seed"
    uv run --frozen python -m coder_agent.eval.swebench_docker --instances "$FORMAL/formal.jsonl" \
      --output "$OUT/$name" --label "wp1-dev-$name" --preset C3 --llm-profile "$PROFILE" \
      --experiment-config "{\"policy_mode\":\"legacy\",\"model_seed\":$seed,\"retry_policy\":\"$policy\"}" \
      --max-steps 40 --order-seed "$ORDER_SEED" --workers "$WORKERS" --retry-errors --max-attempts "$MAX_ATTEMPTS"
    # Grade every prediction afresh: the harness skips instances that already
    # have a report under this run ID, which a retried instance's earlier patch may.
    rm -rf "$OUT/$name/logs/run_evaluation/wp1-dev-$name"
    wp1_grade "$OUT/$name" "wp1-dev-$name"
  done
done

# Every seed with both arms graded, including those an earlier invocation ran.
arms=() baseline=() candidate=()
for dir in "$OUT"/DA-s*; do
  seed="${dir##*-s}"
  if [ -f "$dir/harness_report.json" ] && [ -f "$OUT/DC-s$seed/harness_report.json" ]; then
    arms+=(--arm "DA-s$seed=$dir" --arm "DC-s$seed=$OUT/DC-s$seed")
    baseline+=("DA-s$seed")
    candidate+=("DC-s$seed")
  fi
done
uv run --frozen python -m coder_agent.eval.swebench_summary --instances "$FORMAL/formal.jsonl" "${arms[@]}" \
  --output "$OUT/summary.json" --markdown "$OUT/summary.md"
uv run --frozen python -m coder_agent.eval.swebench_pooled --summary "$OUT/summary.json" \
  --baseline "$(IFS=,; echo "${baseline[*]}")" --candidate "$(IFS=,; echo "${candidate[*]}")" \
  --output "$OUT/pooled.json"
