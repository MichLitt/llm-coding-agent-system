#!/usr/bin/env bash
# WP1 arm R: mini-SWE-agent 2.4.6 on the same instances, model, request settings
# and step limit as arm A, graded by the same harness.
#
#   scripts/wp1_reference_arm.sh INSTANCES_JSONL OUTPUT_DIR LABEL
#
# Run from llm-coding-agent-system/ on the Linux host. mini-SWE-agent gets its
# own venv (MINI_VENV); the GLM key is read from .env and passed only through
# the environment.
set -euo pipefail
source "$(dirname "$0")/wp1_lib.sh"

INSTANCES="$1"
OUT="$2"
LABEL="$3"
MINI_VERSION="2.4.6"
# mini-SWE-agent sets no upper bound on litellm, and litellm 1.106.5 fails on
# import without the optional mcp package. Pin the last stable litellm release
# before mini-SWE-agent 2.4.6.
LITELLM_VERSION="1.93.0"
MINI_VENV="${MINI_VENV:-$HOME/mini-swe-agent-$MINI_VERSION}"
CONFIG="scripts/wp1_mini_swe_agent.yaml"

wp1_preflight
if [ ! -x "$MINI_VENV/bin/mini-extra" ]; then
  uv venv --python 3.12 "$MINI_VENV"
fi
uv pip install --python "$MINI_VENV/bin/python" "mini-swe-agent==$MINI_VERSION" "litellm==$LITELLM_VERSION"
"$MINI_VENV/bin/python" -I -c "
import importlib.metadata as m, sys
got = {name: m.version(name) for name in ('mini-swe-agent', 'litellm')}
want = {'mini-swe-agent': '$MINI_VERSION', 'litellm': '$LITELLM_VERSION'}
sys.exit(0 if got == want else f'versions {got} != {want}')"

API_BASE="$(uv run --frozen python -c "from coder_agent.config import resolve_llm_profile; print(resolve_llm_profile('glm_5_nothink').base_url)")"
OPENAI_API_KEY="$(uv run --frozen python -c "from coder_agent.config import resolve_llm_profile; print(resolve_llm_profile('glm_5_nothink').api_key)")"
export OPENAI_API_KEY
export MSWEA_COST_TRACKING=ignore_errors

# Importing minisweagent prints a banner first; the path is the last line.
BASE_CONFIG="$("$MINI_VENV/bin/python" -I -c "from minisweagent.run.benchmarks.swebench import DEFAULT_CONFIG_FILE; print(DEFAULT_CONFIG_FILE)" | tail -n 1)"
SPECS=("$BASE_CONFIG" "$CONFIG" "model.model_kwargs.api_base=$API_BASE")
"$MINI_VENV/bin/python" -I scripts/wp1_reference_preflight.py "${SPECS[@]}"

mkdir -p "$OUT"
cp "$CONFIG" "$OUT/arm-config.yaml"
cp "$BASE_CONFIG" "$OUT/base-config.yaml"
uv pip freeze --python "$MINI_VENV/bin/python" > "$OUT/requirements.lock"
FILTER="^($(paste -sd'|' <(python3 -c "import json, sys; [print(json.loads(l)['instance_id']) for l in open(sys.argv[1])]" "$INSTANCES")))\$"

"$MINI_VENV/bin/mini-extra" swebench --subset "$DATASET" --split test --filter "$FILTER" \
  -c "${SPECS[0]}" -c "${SPECS[1]}" -c "${SPECS[2]}" \
  -o "$OUT" -w "${WORKERS:-2}"

# preds.json is keyed by instance id; the harness gets one JSONL line per instance.
python3 - "$OUT/preds.json" "$OUT/predictions.jsonl" "$LABEL" <<'PY'
import json, sys
preds = json.load(open(sys.argv[1]))
with open(sys.argv[2], "w") as handle:
    for instance_id in sorted(preds):
        handle.write(json.dumps({"instance_id": instance_id, "model_name_or_path": sys.argv[3],
                                 "model_patch": preds[instance_id].get("model_patch") or ""}) + "\n")
PY

wp1_grade "$OUT" "$LABEL"
