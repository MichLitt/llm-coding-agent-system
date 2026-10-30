#!/usr/bin/env bash
# v0.8.1 SWE-bench harness development check
# (report/IMPROVEMENT_PLAN_v0.8.1-swe-harness.md, section 4). Exploratory: not
# a pre-registered result.
#
# Draws dev2: 100 SWE-bench Verified instances outside the pilot and formal
# samples (seed 20261030); the other 290 are the confirmation pool and stay
# unused. Then runs, with two seeds each:
#   B  C3 with retry_policy=consecutive, the best configuration so far;
#   H  B plus every v0.8.1 harness switch;
# and arm R (mini-SWE-agent) once, grades each, and compares H with B pooled
# per instance.
#
# Run on the Linux x86_64 host from llm-coding-agent-system/:
#
#   scripts/wp1_dev_harness.sh
#
# Re-running resumes; provider and infrastructure failures are retried up to
# MAX_ATTEMPTS attempts in total. Overrides: PROFILE (default
# deepseek_flash_nothink), SEEDS, OUT, SAMPLE_DIR, WORKERS, MAX_ATTEMPTS,
# WITH_R (default 1), HARNESS_PY, HARNESS_WORKERS.
set -euo pipefail
source "$(dirname "$0")/wp1_lib.sh"

PROFILE="${PROFILE:-deepseek_flash_nothink}"
SAMPLE_DIR="${SAMPLE_DIR:-runs/wp1/dev2}"
OUT="${OUT:-runs/wp1/dev2-$PROFILE}"
SEEDS="${SEEDS:-101 202}"
WORKERS="${WORKERS:-4}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-8}"
WITH_R="${WITH_R:-1}"
SAMPLE_SEED=20261030
ORDER_SEED=20261030
BASE='"policy_mode":"legacy","retry_policy":"consecutive"'
HARNESS='"error_signals":"semantic","edit_lock":false,"prompt_profile":"swebench","step_budget_notices":true,"finish_check":true,"max_observation_chars":10000,"max_tool_calls_per_reply":20'

wp1_dev_preflight "$PROFILE"
mkdir -p "$SAMPLE_DIR" "$OUT"
if [ ! -f "$SAMPLE_DIR/dev2.jsonl" ]; then
  # Excluded: the pilot ids and the formal sample, both recorded in the formal meta.
  python3 -c "
import json
meta = json.load(open('artifacts/wp1-formal/formal.meta.json'))
print('\n'.join(sorted(set(meta['excluded']) | set(meta['instance_ids']))))" > "$SAMPLE_DIR/exclude-ids.txt"
  "$HARNESS_PY" scripts/export_swebench_instances.py --dataset "$DATASET" \
    --sample 100 --seed "$SAMPLE_SEED" --exclude "$SAMPLE_DIR/exclude-ids.txt" --output "$SAMPLE_DIR/dev2.jsonl"
fi

for seed in $SEEDS; do
  for arm in B H; do
    config="{$BASE,\"model_seed\":$seed"
    if [ "$arm" = H ]; then
      config="$config,$HARNESS"
    fi
    name="$arm-s$seed"
    uv run --frozen python -m coder_agent.eval.swebench_docker --instances "$SAMPLE_DIR/dev2.jsonl" \
      --output "$OUT/$name" --label "wp1-dev2-$name" --preset C3 --llm-profile "$PROFILE" \
      --experiment-config "$config}" --max-steps 40 --order-seed "$ORDER_SEED" \
      --workers "$WORKERS" --retry-errors --max-attempts "$MAX_ATTEMPTS"
    # Grade every prediction afresh: the harness skips instances that already
    # have a report under this run ID, which a retried instance's earlier patch may.
    rm -rf "$OUT/$name/logs/run_evaluation/wp1-dev2-$name"
    wp1_grade "$OUT/$name" "wp1-dev2-$name"
  done
done

if [ "$WITH_R" = 1 ] && [ ! -f "$OUT/R/harness_report.json" ]; then
  PROFILE="$PROFILE" scripts/wp1_reference_arm.sh "$SAMPLE_DIR/dev2.jsonl" "$OUT/R" wp1-dev2-R
fi

# Every seed with both arms graded, including those an earlier invocation ran.
arms=() baseline=() candidate=()
for dir in "$OUT"/B-s*; do
  seed="${dir##*-s}"
  if [ -f "$dir/harness_report.json" ] && [ -f "$OUT/H-s$seed/harness_report.json" ]; then
    arms+=(--arm "B-s$seed=$dir" --arm "H-s$seed=$OUT/H-s$seed")
    baseline+=("B-s$seed")
    candidate+=("H-s$seed")
  fi
done
if [ -f "$OUT/R/harness_report.json" ]; then
  arms+=(--arm "R=$OUT/R")
fi
uv run --frozen python -m coder_agent.eval.swebench_summary --instances "$SAMPLE_DIR/dev2.jsonl" "${arms[@]}" \
  --output "$OUT/summary.json" --markdown "$OUT/summary.md"
uv run --frozen python -m coder_agent.eval.swebench_pooled --summary "$OUT/summary.json" \
  --baseline "$(IFS=,; echo "${baseline[*]}")" --candidate "$(IFS=,; echo "${candidate[*]}")" \
  --output "$OUT/pooled.json"
