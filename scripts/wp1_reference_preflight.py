"""Check arm R's merged mini-SWE-agent config before a WP1 run.

Run with mini-SWE-agent's own interpreter and the same -c specs the run uses:

    $MINI_VENV/bin/python scripts/wp1_reference_preflight.py BASE_CONFIG ARM_CONFIG model.model_kwargs.api_base=URL

The merged config must match arm A's protocol: step limit 40, no network, and
GLM thinking disabled. The last check sends one 64-token request through
mini-SWE-agent's own model class (same tools and kwargs as the run). With
thinking on, GLM spends those tokens reasoning and stops at the length limit;
with thinking off, it answers with a few reasoning tokens at most.
"""

import sys

from minisweagent.config import get_config_from_spec
from minisweagent.models import get_model
from minisweagent.utils.serialize import recursive_merge


def main(specs: list[str]) -> int:
    config = recursive_merge(*[get_config_from_spec(spec) for spec in specs])
    agent, environment, model = config["agent"], config["environment"], config["model"]
    kwargs = model.get("model_kwargs", {})
    checks = {
        "agent.step_limit == 40": agent.get("step_limit") == 40,
        "environment runs with --network none": "--network" in environment.get("run_args", [])
        and environment["run_args"][environment["run_args"].index("--network") + 1] == "none",
        "thinking disabled": kwargs.get("extra_body", {}).get("thinking", {}).get("type") == "disabled",
        "max_tokens == 8192": kwargs.get("max_tokens") == 8192,
        "api_base set": bool(kwargs.get("api_base")),
    }
    for name, ok in checks.items():
        print(f"preflight: {'ok  ' if ok else 'FAIL'} {name}")
    if not all(checks.values()):
        return 1

    llm = get_model(config={**model, "model_kwargs": {**kwargs, "max_tokens": 64}})
    reply = llm._query([{"role": "user", "content": "Reply with OK."}])
    choice = reply.choices[0]
    details = getattr(reply.usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) or 0
    print(f"preflight: live request finish_reason={choice.finish_reason} reasoning_tokens={reasoning}")
    if choice.finish_reason == "length" or reasoning >= 16:
        print("preflight: FAIL thinking does not look disabled on the live request")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
