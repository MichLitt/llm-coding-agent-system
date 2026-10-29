# Shared helpers for the WP1 scripts. Source it from llm-coding-agent-system/.

HARNESS_PY="${HARNESS_PY:-$HOME/swebench-harness/.venv/bin/python}"
DATASET="SWE-bench/SWE-bench_Verified"
# Pre-registered endpoint: the GLM Coding Plan. The same key on the general
# endpoint (/api/paas/v4) fails with 429 code 1113 "Insufficient balance".
WP1_API_BASE="https://api.z.ai/api/coding/paas/v4"

wp1_preflight() {
  if [ -n "${CODER_MAX_TOKENS:-}" ]; then
    echo "CODER_MAX_TOKENS must be unset: the pre-registered budget comes from config.yaml" >&2
    return 1
  fi
  # One small request through the Agent's own client: the key, the endpoint and
  # the account's package must work before any container starts.
  uv run --frozen python -c "
import asyncio, sys
from coder_agent.config import resolve_llm_profile
from coder_agent.core.llm_client import LLMClient

profile = resolve_llm_profile('glm_5_nothink')
if not profile.api_key:
    sys.exit('LLM_GLM_5_API_KEY is not set; add it to .env')
if profile.base_url != '$WP1_API_BASE':
    sys.exit(f'LLM base URL is {profile.base_url}, but WP1 is registered on $WP1_API_BASE; fix LLM_GLM_5_BASE_URL in .env')

async def probe():
    client = LLMClient(profile=profile)
    try:
        await client.chat(messages=[{'role': 'user', 'content': 'Reply with OK.'}], system='', tools=[],
                          model=profile.model, max_tokens=16, temperature=0.0)
    finally:
        await client.aclose()

try:
    asyncio.run(probe())
except Exception as exc:
    sys.exit(f'arm A preflight: LLM request failed: {type(exc).__name__}: {str(exc)[:300]}')
print(f'arm A preflight: ok ({profile.model} at {profile.base_url})')"
  docker info >/dev/null
  "$HARNESS_PY" -c "import swebench; print('swebench', swebench.__version__)"
}

# wp1_dev_preflight PROFILE: wp1_preflight for the exploratory runs, on any
# profile and endpoint (wp1_preflight stays as the formal run used it). The
# probe sends a model seed, as the runs do, so a provider that rejects one
# fails here rather than in every instance.
wp1_dev_preflight() {
  local profile="$1"
  if [ -n "${CODER_MAX_TOKENS:-}" ]; then
    echo "CODER_MAX_TOKENS must be unset: the budget comes from config.yaml" >&2
    return 1
  fi
  uv run --frozen python -c "
import asyncio, sys
from coder_agent.config import resolve_llm_profile
from coder_agent.core.llm_client import LLMClient

profile = resolve_llm_profile('$profile')
if not profile.api_key:
    sys.exit('$profile has no API key: add the variable its api_key_env names in config.yaml to .env')

async def probe():
    client = LLMClient(profile=profile)
    try:
        await client.chat(messages=[{'role': 'user', 'content': 'Reply with OK.'}], system='', tools=[],
                          model=profile.model, max_tokens=16, temperature=0.0, seed=101)
    finally:
        await client.aclose()

try:
    asyncio.run(probe())
except Exception as exc:
    sys.exit(f'$profile preflight: LLM request failed: {type(exc).__name__}: {str(exc)[:300]}')
print(f'$profile preflight: ok ({profile.model} at {profile.base_url})')"
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
