"""Run the Agent on SWE-bench instances inside their official Docker images.

Each instance gets a fresh container from its official image
(``swebench/sweb.eval.x86_64.<id>``). The Agent's tools run inside it, and the
final ``git diff`` of ``/testbed`` becomes the prediction. Predictions use the
official format, so grading is done by the unmodified SWE-bench harness:

    python -m swebench.harness.run_evaluation --dataset_name <dataset> \\
        --predictions_path <output>/predictions.jsonl --run_id <label>

The Agent sees only the problem statement, never the hidden FAIL_TO_PASS or
PASS_TO_PASS tests. Instances come from a JSONL file written by
``scripts/export_swebench_instances.py``; this keeps ``datasets`` out of the
Agent's dependencies. Re-running with the same output directory skips
instances that already have a prediction.

    uv run python -m coder_agent.eval.swebench_docker --instances instances.jsonl \\
        --output runs/verified-c3-s101 --label verified-c3-s101 --preset C3 --llm-profile glm_5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from coder_agent.memory.run_state import current_git_commit
from coder_agent.tools.container import (
    SWEBENCH_ENV_SETUP,
    DockerSandbox,
    build_container_tools,
    swebench_image,
)

PROMPT_TEMPLATE = """<issue>
{problem_statement}
</issue>

The repository is at /testbed, checked out at the commit where this issue was reported.
Change the non-test source files so that the issue is resolved. Do not modify existing
test files. You may write a small script to reproduce the issue, but delete it before you
finish. When you are done, reply with a short summary of the change."""


def load_instances(path: Path) -> list[dict[str, Any]]:
    instances = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for instance in instances:
        missing = {"instance_id", "problem_statement"} - instance.keys()
        if missing:
            raise ValueError(f"instance record missing {sorted(missing)}: {instance.get('instance_id')}")
    return instances


def _completed_ids(predictions: Path) -> set[str]:
    if not predictions.exists():
        return set()
    return {json.loads(line)["instance_id"] for line in predictions.read_text().splitlines() if line.strip()}


def _default_start_sandbox(instance_id: str, options: dict[str, Any]) -> DockerSandbox:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", instance_id)
    return DockerSandbox.start(
        swebench_image(instance_id, options["namespace"]),
        name=f"coder-agent-{safe}-{uuid.uuid4().hex[:6]}",
        env_setup=SWEBENCH_ENV_SETUP,
        network=options["network"],
        cpus=options.get("cpus"),
        memory=options.get("memory"),
    )


def _default_make_agent(sandbox: DockerSandbox, options: dict[str, Any]) -> Any:
    from coder_agent.cli.factory import make_agent, make_run_state_store, make_trajectory_store, resolve_agent_config

    return make_agent(
        resolve_agent_config(options["preset"]),
        workspace=Path(sandbox.workdir),
        experiment_config=options["experiment_config"],
        llm_profile=options["llm_profile"],
        no_memory=True,
        experiment_id=options["label"],
        trajectory_store=make_trajectory_store(str(options["output"] / "trajectories")),
        run_state_store=make_run_state_store(options["output"] / "run_state.db"),
        preset_name=options["preset"],
        tools=build_container_tools(sandbox),
    )


def run_instance(
    instance: dict[str, Any],
    options: dict[str, Any],
    *,
    start_sandbox: Callable[[str, dict[str, Any]], DockerSandbox] = _default_start_sandbox,
    agent_factory: Callable[[DockerSandbox, dict[str, Any]], Any] = _default_make_agent,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (prediction, result record) for one instance; failures yield an empty patch."""
    instance_id = instance["instance_id"]
    started = time.time()
    record: dict[str, Any] = {"instance_id": instance_id, "error": None}
    patch = ""
    sandbox = agent = None
    try:
        sandbox = start_sandbox(instance_id, options)
        agent = agent_factory(sandbox, options)
        result = agent.run(
            PROMPT_TEMPLATE.format(problem_statement=instance["problem_statement"]),
            task_id=instance_id,
            max_steps=options["max_steps"],
            record_memory=False,
        )
        patch = sandbox.diff()
        record.update(
            final_status=result.final_status,
            termination_reason=result.termination_reason,
            steps=result.steps,
            usage=result.usage,
            run_id=result.extra.get("run_id"),
        )
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()[-2000:]
    finally:
        if agent is not None and hasattr(agent, "close"):
            agent.close()
        if sandbox is not None:
            sandbox.stop()
    record["patch_bytes"] = len(patch.encode("utf-8"))
    record["duration_seconds"] = round(time.time() - started, 1)
    prediction = {"instance_id": instance_id, "model_name_or_path": options["label"], "model_patch": patch}
    return prediction, record


def run_instances(
    instances: list[dict[str, Any]],
    options: dict[str, Any],
    *,
    workers: int = 1,
    start_sandbox: Callable[[str, dict[str, Any]], DockerSandbox] = _default_start_sandbox,
    agent_factory: Callable[[DockerSandbox, dict[str, Any]], Any] = _default_make_agent,
) -> dict[str, Any]:
    output: Path = options["output"]
    output.mkdir(parents=True, exist_ok=True)
    predictions_path, results_path = output / "predictions.jsonl", output / "results.jsonl"
    done = _completed_ids(predictions_path)
    pending = [instance for instance in instances if instance["instance_id"] not in done]
    lock = threading.Lock()

    def work(instance: dict[str, Any]) -> None:
        prediction, record = run_instance(instance, options, start_sandbox=start_sandbox, agent_factory=agent_factory)
        with lock:  # results first: a prediction marks the instance complete
            with results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            with predictions_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(prediction) + "\n")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, pending))
    return {"skipped_completed": len(done), "ran": len(pending), "predictions": str(predictions_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--preset", default="C3")
    parser.add_argument("--llm-profile", required=True)
    parser.add_argument("--experiment-config", default="{}")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--namespace", default="swebench")
    parser.add_argument("--network", default="none")
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="6g")
    args = parser.parse_args(argv)

    instances = load_instances(args.instances)[: args.limit]
    options = {
        "output": args.output, "label": args.label, "preset": args.preset, "llm_profile": args.llm_profile,
        "experiment_config": json.loads(args.experiment_config), "max_steps": args.max_steps,
        "namespace": args.namespace, "network": args.network, "cpus": args.cpus, "memory": args.memory,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    manifest = {
        "schema_version": "swebench-docker-run/v1",
        **{key: value for key, value in options.items() if key != "output"},
        "instances_file_sha256": hashlib.sha256(args.instances.read_bytes()).hexdigest(),
        "instance_ids": [instance["instance_id"] for instance in instances],
        "agent_commit": current_git_commit(),
        "workers": args.workers,
        "started_at": time.time(),
    }
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        frozen = ("instances_file_sha256", "instance_ids", "preset", "llm_profile", "experiment_config", "max_steps")
        changed = [key for key in frozen if previous.get(key) != manifest[key]]
        if changed:
            raise SystemExit(f"resume refused: {changed} differ from the existing manifest")
        manifest["started_at"] = previous["started_at"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    summary = run_instances(instances, options, workers=args.workers)
    manifest["finished_at"] = time.time()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
