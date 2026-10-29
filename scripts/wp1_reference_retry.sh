#!/usr/bin/env bash
# WP1 arm R retry (report/PREREG_WP1_SWEBENCH_VERIFIED_DRAFT.md, Amendment 2):
# rerun once, with identical settings, the arm-R instances whose first attempt
# ended in a provider failure, such as the GLM 5-hour quota (429 code 1308).
# Then regrade arm R and rebuild the summary.
#
# Run on the Linux x86_64 host from llm-coding-agent-system/, after the formal run:
#
#   scripts/wp1_reference_retry.sh
#
# The first attempt stays in place. The retry writes to R/retry-1/, and each
# instance's last attempt counts. A provider failure in the retry is final.
# Re-running resumes the retry. Overrides: OUT, WORKERS, HARNESS_PY,
# HARNESS_WORKERS, MINI_VENV.
set -euo pipefail
source "$(dirname "$0")/wp1_lib.sh"

OUT="${OUT:-runs/wp1/formal}"
R="$OUT/R"
LABEL="wp1-formal-R"
PREREG="report/PREREG_WP1_SWEBENCH_VERIFIED_DRAFT.md"
MINI_VENV="${MINI_VENV:-$HOME/mini-swe-agent-2.4.6}"

if ! grep -q '^### Amendment 2' "$PREREG" || ! git diff --quiet HEAD -- "$PREREG"; then
  echo "commit Amendment 2 to $PREREG before retrying arm R" >&2
  exit 1
fi
[ -f "$R/harness_report.json" ] || { echo "$R has not been graded yet; finish scripts/wp1_formal.sh first" >&2; exit 1; }

# Chosen once, from the first attempt only, so a resumed retry keeps the same set.
if [ ! -f "$R/retry-ids.txt" ]; then
  uv run --frozen python -m coder_agent.eval.mini_swe_agent retry-ids --run-dir "$R" > "$R/retry-ids.pending.txt"
  mv "$R/retry-ids.pending.txt" "$R/retry-ids.txt"
fi
if [ ! -s "$R/retry-ids.txt" ]; then
  echo "no provider failures in $R: nothing to retry"
  exit 0
fi
echo "retrying $(grep -c . "$R/retry-ids.txt") arm-R instances"

# Identical settings: the first attempt's package versions and its archived configs.
uv pip freeze --python "$MINI_VENV/bin/python" | diff "$R/requirements.lock" - \
  || { echo "$MINI_VENV differs from $R/requirements.lock" >&2; exit 1; }
wp1_preflight
API_BASE="$(uv run --frozen python -c "from coder_agent.config import resolve_llm_profile; print(resolve_llm_profile('glm_5_nothink').base_url)")"
OPENAI_API_KEY="$(uv run --frozen python -c "from coder_agent.config import resolve_llm_profile; print(resolve_llm_profile('glm_5_nothink').api_key)")"
export OPENAI_API_KEY
export MSWEA_COST_TRACKING=ignore_errors
SPECS=("$R/base-config.yaml" "$R/arm-config.yaml" "model.model_kwargs.api_base=$API_BASE")
"$MINI_VENV/bin/python" -I scripts/wp1_reference_preflight.py "${SPECS[@]}"

FILTER="^($(paste -sd'|' "$R/retry-ids.txt"))\$"
"$MINI_VENV/bin/mini-extra" swebench --subset "$DATASET" --split test --filter "$FILTER" \
  -c "${SPECS[0]}" -c "${SPECS[1]}" -c "${SPECS[2]}" \
  -o "$R/retry-1" -w "${WORKERS:-2}"

# Keep the first attempt's predictions, grading and summary next to the new ones.
for file in "$R/predictions.jsonl" "$R/harness_report.json" "$OUT/summary.json" "$OUT/summary.md"; do
  [ -f "${file%.*}.attempt1.${file##*.}" ] || cp "$file" "${file%.*}.attempt1.${file##*.}"
done
uv run --frozen python -m coder_agent.eval.mini_swe_agent merge-predictions --run-dir "$R" --label "$LABEL" \
  --output "$R/predictions.jsonl"
# Same run id: the harness reuses its reports for unchanged predictions and
# grades the retried ones.
wp1_grade "$R" "$LABEL"

uv run --frozen python -m coder_agent.eval.swebench_summary --instances "$OUT/formal.jsonl" \
  --arm "A-s101=$OUT/A-s101" --arm "A-s202=$OUT/A-s202" --arm "R=$R" \
  --output "$OUT/summary.json" --markdown "$OUT/summary.md"
