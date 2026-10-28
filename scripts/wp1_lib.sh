# Shared helpers for the WP1 scripts. Source it from llm-coding-agent-system/.

HARNESS_PY="${HARNESS_PY:-$HOME/swebench-harness/.venv/bin/python}"
DATASET="SWE-bench/SWE-bench_Verified"

wp1_preflight() {
  if [ -n "${CODER_MAX_TOKENS:-}" ]; then
    echo "CODER_MAX_TOKENS must be unset: the pre-registered budget comes from config.yaml" >&2
    return 1
  fi
  uv run --frozen python -c "
import sys
from coder_agent.config import resolve_llm_profile
sys.exit(0 if resolve_llm_profile('glm_5_nothink').api_key else 'LLM_GLM_5_API_KEY is not set; add it to .env')"
  docker info >/dev/null
  "$HARNESS_PY" -c "import swebench; print('swebench', swebench.__version__)"
}

# wp1_grade ARM_DIR LABEL: grade ARM_DIR/predictions.jsonl with the unmodified
# harness and copy its report to ARM_DIR/harness_report.json.
wp1_grade() {
  local arm_dir="$1" label="$2" report
  (
    cd "$arm_dir"
    "$HARNESS_PY" -m swebench.harness.run_evaluation --dataset_name "$DATASET" \
      --predictions_path predictions.jsonl --run_id "$label" --max_workers "${HARNESS_WORKERS:-4}"
  )
  # swebench 5.0.x writes <model>.<run_id>.json in the working directory; newer
  # versions write logs/run_evaluation/<run_id>/results.json.
  report="$arm_dir/$label.$label.json"
  [ -f "$report" ] || report="$arm_dir/logs/run_evaluation/$label/results.json"
  cp "$report" "$arm_dir/harness_report.json"
}
