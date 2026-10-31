#!/usr/bin/env bash
# v0.8.1 SWE-bench harness development check
# (report/IMPROVEMENT_PLAN_v0.8.1-swe-harness.md, section 4). Exploratory: not
# a pre-registered result.
#
# Draws dev2: 100 SWE-bench Verified instances outside the pilot and formal
# samples (seed 20261030); the other 290 are the confirmation pool and stay
# unused. Then runs the arms in ARMS with two seeds each:
#   B   C3 with retry_policy=consecutive, the best configuration so far;
#   H   B plus every v0.8.1 harness switch;
#   HN  H without the stopping nudges (step_budget_notices, finish_check);
#   M   a plain loop: the SWE-bench prompt, no correction hints, retry guard,
#       doom-loop warning or edit lock, with the output and call caps;
# and arm R (mini-SWE-agent) once. It grades the arms it ran and compares each
# of H, HN and M with B pooled per instance (pooled-<arm>.json).
#
# Run on the Linux x86_64 host from llm-coding-agent-system/:
#
#   scripts/wp1_dev_harness.sh                # first round: B and H
#   ARMS="HN M" scripts/wp1_dev_harness.sh    # ablation round
#
# Re-running resumes; provider and infrastructure failures are retried up to
# MAX_ATTEMPTS attempts in total. Overrides: PROFILE (default
# deepseek_flash_nothink), ARMS (default "B H"), SEEDS, OUT, SAMPLE_DIR,
# WORKERS, MAX_ATTEMPTS, WITH_R (default 1), HARNESS_PY, HARNESS_WORKERS.
set -euo pipefail
source "$(dirname "$0")/wp1_lib.sh"

PROFILE="${PROFILE:-deepseek_flash_nothink}"
SAMPLE_DIR="${SAMPLE_DIR:-runs/wp1/dev2}"
OUT="${OUT:-runs/wp1/dev2-$PROFILE}"
ARMS="${ARMS:-B H}"
SEEDS="${SEEDS:-101 202}"
WORKERS="${WORKERS:-4}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-8}"
WITH_R="${WITH_R:-1}"
SAMPLE_SEED=20261030
ORDER_SEED=20261030
BASE='"policy_mode":"legacy","retry_policy":"consecutive"'
CAPS='"max_observation_chars":10000,"max_tool_calls_per_reply":20'
QUIET="\"error_signals\":\"semantic\",\"edit_lock\":false,\"prompt_profile\":\"swebench\",$CAPS"
NUDGES='"step_budget_notices":true,"finish_check":true'
PLAIN="\"policy_mode\":\"legacy\",\"retry_policy\":\"off\",\"prompt_profile\":\"swebench\",\"edit_lock\":false,\"correction_hints\":false,\"doom_loop_threshold\":0,$CAPS"

arm_config() {  # arm_config ARM SEED: the arm's --experiment-config
  case "$1" in
    B) echo "{$BASE,\"model_seed\":$2}" ;;
    H) echo "{$BASE,\"model_seed\":$2,$QUIET,$NUDGES}" ;;
    HN) echo "{$BASE,\"model_seed\":$2,$QUIET}" ;;
    M) echo "{$PLAIN,\"model_seed\":$2}" ;;
    *) echo "unknown arm $1" >&2; return 1 ;;
  esac
}

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
  for arm in $ARMS; do
    config="$(arm_config "$arm" "$seed")"
    name="$arm-s$seed"
    uv run --frozen python -m coder_agent.eval.swebench_docker --instances "$SAMPLE_DIR/dev2.jsonl" \
      --output "$OUT/$name" --label "wp1-dev2-$name" --preset C3 --llm-profile "$PROFILE" \
      --experiment-config "$config" --max-steps 40 --order-seed "$ORDER_SEED" \
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

# Every graded arm and seed, including those an earlier invocation ran.
arms=()
for arm in B H HN M; do
  for seed in $SEEDS; do
    if [ -f "$OUT/$arm-s$seed/harness_report.json" ]; then
      arms+=(--arm "$arm-s$seed=$OUT/$arm-s$seed")
    fi
  done
done
if [ -f "$OUT/R/harness_report.json" ]; then
  arms+=(--arm "R=$OUT/R")
fi
uv run --frozen python -m coder_agent.eval.swebench_summary --instances "$SAMPLE_DIR/dev2.jsonl" "${arms[@]}" \
  --output "$OUT/summary.json" --markdown "$OUT/summary.md"
# Each candidate against B, over the seeds where both are graded.
for arm in H HN M; do
  baseline=() candidate=()
  for seed in $SEEDS; do
    if [ -f "$OUT/B-s$seed/harness_report.json" ] && [ -f "$OUT/$arm-s$seed/harness_report.json" ]; then
      baseline+=("B-s$seed")
      candidate+=("$arm-s$seed")
    fi
  done
  if [ "${#candidate[@]}" -gt 0 ]; then
    uv run --frozen python -m coder_agent.eval.swebench_pooled --summary "$OUT/summary.json" \
      --baseline "$(IFS=,; echo "${baseline[*]}")" --candidate "$(IFS=,; echo "${candidate[*]}")" \
      --output "$OUT/pooled-$arm.json"
  fi
done
