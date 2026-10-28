#!/usr/bin/env bash
# WP1 formal run (report/PREREG_WP1_SWEBENCH_VERIFIED_DRAFT.md). It refuses to
# start until the pre-registration is frozen: committed, marked frozen, no TBD.
#
# Run on the Linux x86_64 host from llm-coding-agent-system/, with the GLM key in .env:
#
#   scripts/wp1_formal.sh
#
# Arms run in order A-s101, A-s202, R. Budget checkpoint (§7): after the first
# 25 instances of A-s101, whole-run usage is projected, and the run stops if it
# exceeds 45M input or 1M output tokens. Re-running resumes every step.
# Overrides: HARNESS_PY, OUT, WORKERS, HARNESS_WORKERS, PILOT_IDS.
set -euo pipefail
source "$(dirname "$0")/wp1_lib.sh"

OUT="${OUT:-runs/wp1/formal}"
WORKERS="${WORKERS:-2}"
PILOT_IDS="${PILOT_IDS:-runs/wp1/pilot/pilot-ids.txt}"
PREREG="report/PREREG_WP1_SWEBENCH_VERIFIED_DRAFT.md"
ORDER_SEED=20261029
INSTANCE_RUNS=300  # A-s101 + A-s202 + R, 100 instances each

if ! grep -q '^Status: \*\*frozen' "$PREREG" || grep -q 'TBD' "$PREREG" || ! git diff --quiet HEAD -- "$PREREG"; then
  echo "$PREREG is not frozen (status, TBD items or uncommitted edits); freeze it before the formal run" >&2
  exit 1
fi
wp1_preflight
mkdir -p "$OUT"
if [ ! -f "$OUT/formal.jsonl" ]; then
  [ -f "$PILOT_IDS" ] || { echo "missing $PILOT_IDS, the pilot ids to exclude" >&2; exit 1; }
  "$HARNESS_PY" scripts/export_swebench_instances.py --dataset "$DATASET" \
    --sample 100 --seed 20261028 --exclude "$PILOT_IDS" --output "$OUT/formal.jsonl"
fi

run_arm_a() {  # run_arm_a SEED [extra runner arguments]
  local seed="$1"
  shift
  uv run --frozen python -m coder_agent.eval.swebench_docker --instances "$OUT/formal.jsonl" \
    --output "$OUT/A-s$seed" --label "wp1-formal-A-s$seed" --preset C3 --llm-profile glm_5_nothink \
    --experiment-config "{\"policy_mode\":\"legacy\",\"model_seed\":$seed}" --max-steps 40 \
    --order-seed "$ORDER_SEED" --workers "$WORKERS" --retry-errors "$@"
}

if [ ! -f "$OUT/budget-checkpoint.json" ]; then
  run_arm_a 101 --stop-after 25
  if ! uv run --frozen python -m coder_agent.eval.swebench_budget --run-dir "$OUT/A-s101" \
      --instance-runs "$INSTANCE_RUNS" --max-input-tokens 45000000 --max-output-tokens 1000000 \
      > "$OUT/budget-checkpoint.pending.json"; then
    cat "$OUT/budget-checkpoint.pending.json"
    echo "budget checkpoint not passed: stop and re-evaluate (pre-registration §7)" >&2
    exit 1
  fi
  mv "$OUT/budget-checkpoint.pending.json" "$OUT/budget-checkpoint.json"
fi
cat "$OUT/budget-checkpoint.json"

run_arm_a 101
wp1_grade "$OUT/A-s101" wp1-formal-A-s101
run_arm_a 202
wp1_grade "$OUT/A-s202" wp1-formal-A-s202
scripts/wp1_reference_arm.sh "$OUT/formal.jsonl" "$OUT/R" wp1-formal-R

uv run --frozen python -m coder_agent.eval.swebench_summary --instances "$OUT/formal.jsonl" \
  --arm "A-s101=$OUT/A-s101" --arm "A-s202=$OUT/A-s202" --arm "R=$OUT/R" \
  --output "$OUT/summary.json" --markdown "$OUT/summary.md"
