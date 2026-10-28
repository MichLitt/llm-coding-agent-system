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
instances that already have a prediction. With ``--retry-errors``, an instance
whose attempt hit an infrastructure or provider failure is run once more with
identical settings, in the same invocation (at most two attempts in total).

Each instance has a wall-clock cap (default 30 minutes). A run that reaches
it is stopped at the next step boundary and counted as a budget failure: its
prediction is an empty patch, and the patch it had is kept under
``discarded_patches/`` for audit.

    uv run python -m coder_agent.eval.swebench_docker --instances instances.jsonl \\
        --output runs/verified-c3-s101 --label verified-c3-s101 --preset C3 --llm-profile glm_5_nothink
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

from coder_agent.config import cfg, resolve_llm_profile
from coder_agent.memory.run_state import current_git_commit
from coder_agent.tools.container import (
    SWEBENCH_CONTAINER_ENV,
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

DEFAULT_WALL_CAP_SECONDS = 30 * 60
MAX_ATTEMPTS = 2

# LLM exceptions that mean the provider failed, not the Agent. A truncated
# reply (LLMOutputTruncatedError) or a stream that ran past its total bound is
# model behavior and stays an Agent failure.
_PROVIDER_ERROR_CLASSES = (
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "APIStatusError",
)


def is_provider_failure(record: dict[str, Any]) -> bool:
    if record.get("termination_reason") != "loop_exception":
        return False
    detail = "\n".join(record.get("error_details") or [])
    if "Exception stage: llm.chat" not in detail:
        return False
    if any(f"Exception class: {name}" in detail for name in _PROVIDER_ERROR_CLASSES):
        return True
    return "Exception class: TimeoutError" in detail and "idle" in detail


def needs_retry(record: dict[str, Any]) -> bool:
    """Infrastructure or provider failure: retried once with identical settings."""
    return bool(record.get("error")) or is_provider_failure(record)


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


def _attempts(results: Path) -> dict[str, list[dict[str, Any]]]:
    attempts: dict[str, list[dict[str, Any]]] = {}
    if results.exists():
        for line in results.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                attempts.setdefault(record["instance_id"], []).append(record)
    return attempts


def _dedupe_predictions(path: Path) -> None:
    """Keep the last prediction per instance, so a retried instance is graded once."""
    if not path.exists():
        return
    latest: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            latest[json.loads(line)["instance_id"]] = line
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{line}\n" for line in latest.values()))
    tmp.replace(path)


_RUN_STATE_STORES: dict[Path, Any] = {}
_RUN_STATE_STORES_LOCK = threading.Lock()


def _shared_run_state_store(path: Path) -> Any:
    """One store per database for the whole run, shared by the worker threads.

    RunStateStore is thread-safe. Opening one per instance raced on first use
    and left every connection open.
    """
    from coder_agent.cli.factory import make_run_state_store

    key = path.resolve()
    with _RUN_STATE_STORES_LOCK:
        if key not in _RUN_STATE_STORES:
            _RUN_STATE_STORES[key] = make_run_state_store(key)
        return _RUN_STATE_STORES[key]


def _safe_name(instance_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", instance_id)


def _default_start_sandbox(instance_id: str, options: dict[str, Any]) -> DockerSandbox:
    return DockerSandbox.start(
        swebench_image(instance_id, options["namespace"]),
        name=f"coder-agent-{_safe_name(instance_id)}-{uuid.uuid4().hex[:6]}",
        env_setup=SWEBENCH_ENV_SETUP,
        env=SWEBENCH_CONTAINER_ENV,
        network=options["network"],
        cpus=options.get("cpus"),
        memory=options.get("memory"),
    )


def _default_make_agent(sandbox: DockerSandbox, options: dict[str, Any]) -> Any:
    from coder_agent.cli.factory import make_agent, make_trajectory_store, resolve_agent_config

    return make_agent(
        resolve_agent_config(options["preset"]),
        workspace=Path(sandbox.workdir),
        experiment_config=options["experiment_config"],
        llm_profile=options["llm_profile"],
        no_memory=True,
        experiment_id=options["label"],
        trajectory_store=make_trajectory_store(str(options["output"] / "trajectories")),
        run_state_store=_shared_run_state_store(options["output"] / "run_state.db"),
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
    record: dict[str, Any] = {"instance_id": instance_id, "error": None, "budget_failure": None}
    patch = ""
    sandbox = agent = None
    wall_cap_hit = threading.Event()
    timer = threading.Timer(options.get("wall_cap_seconds", DEFAULT_WALL_CAP_SECONDS), wall_cap_hit.set)
    timer.daemon = True
    try:
        sandbox = start_sandbox(instance_id, options)
        record["image"] = sandbox.image_provenance()
        agent = agent_factory(sandbox, options)
        timer.start()
        result = agent.run(
            PROMPT_TEMPLATE.format(problem_statement=instance["problem_statement"]),
            task_id=instance_id,
            max_steps=options["max_steps"],
            record_memory=False,
            cancel_event=wall_cap_hit,
        )
        timer.cancel()
        patch = sandbox.diff()
        record.update(
            final_status=result.final_status,
            termination_reason=result.termination_reason,
            error_details=[detail[:1000] for detail in result.error_details[:2]],
            steps=result.steps,
            usage=result.usage,
            run_id=result.extra.get("run_id"),
        )
        if result.termination_reason == "cancelled" and wall_cap_hit.is_set():
            record["budget_failure"] = "wall_cap"
            discarded = options["output"] / "discarded_patches" / f"{_safe_name(instance_id)}.diff"
            discarded.parent.mkdir(parents=True, exist_ok=True)
            discarded.write_text(patch, encoding="utf-8")
            record["discarded_patch_bytes"] = len(patch.encode("utf-8"))
            patch = ""
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()[-2000:]
    finally:
        timer.cancel()
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
    attempts = _attempts(results_path)
    retry = set()
    if options.get("retry_errors"):
        retry = {
            instance_id for instance_id, history in attempts.items()
            if needs_retry(history[-1]) and len(history) < MAX_ATTEMPTS
        }
    pending = [
        instance for instance in instances
        if instance["instance_id"] not in done or instance["instance_id"] in retry
    ]
    lock = threading.Lock()

    def work(instance: dict[str, Any]) -> None:
        prediction, record = run_instance(instance, options, start_sandbox=start_sandbox, agent_factory=agent_factory)
        record["attempt"] = len(attempts.get(instance["instance_id"], [])) + 1
        with lock:  # results first: a prediction marks the instance complete
            with results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            with predictions_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(prediction) + "\n")

    def run_batch(batch: list[dict[str, Any]]) -> None:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(work, batch))

    run_batch(pending)
    retried = len(retry)
    ran = len(pending)
    if options.get("retry_errors"):
        attempts.clear()
        attempts.update(_attempts(results_path))
        second = [
            instance for instance in instances
            if (history := attempts.get(instance["instance_id"]))
            and needs_retry(history[-1]) and len(history) < MAX_ATTEMPTS
        ]
        run_batch(second)
        retried += len(second)
        ran += len(second)
    _dedupe_predictions(predictions_path)
    return {
        "skipped_completed": len(done - retry),
        "ran": ran,
        "retried": retried,
        "predictions": str(predictions_path),
    }


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
    parser.add_argument("--wall-cap-minutes", type=float, default=DEFAULT_WALL_CAP_SECONDS / 60)
    parser.add_argument("--retry-errors", action="store_true",
                        help="run an instance once more when an attempt hits an infrastructure or provider failure")
    args = parser.parse_args(argv)

    instances = load_instances(args.instances)[: args.limit]
    options = {
        "output": args.output, "label": args.label, "preset": args.preset, "llm_profile": args.llm_profile,
        "experiment_config": json.loads(args.experiment_config), "max_steps": args.max_steps,
        "namespace": args.namespace, "network": args.network, "cpus": args.cpus, "memory": args.memory,
        "wall_cap_seconds": args.wall_cap_minutes * 60,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    manifest = {
        "schema_version": "swebench-docker-run/v1",
        **{key: value for key, value in options.items() if key not in ("output", "retry_errors")},
        "instances_file_sha256": hashlib.sha256(args.instances.read_bytes()).hexdigest(),
        "instance_ids": [instance["instance_id"] for instance in instances],
        "agent_commit": current_git_commit(),
        "model_settings": {
            "temperature": cfg.model.temperature,
            "max_tokens": cfg.model.max_tokens,
            "extra_body": resolve_llm_profile(args.llm_profile).extra_body,
            "base_url": resolve_llm_profile(args.llm_profile).base_url,
        },
        "workers": args.workers,
        "started_at": time.time(),
    }
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        frozen = (
            "instances_file_sha256", "instance_ids", "preset", "llm_profile", "experiment_config", "max_steps",
            "model_settings", "wall_cap_seconds",
        )
        changed = [key for key in frozen if previous.get(key) != manifest[key]]
        if changed:
            raise SystemExit(f"resume refused: {changed} differ from the existing manifest")
        manifest["started_at"] = previous["started_at"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    options["retry_errors"] = args.retry_errors
    summary = run_instances(instances, options, workers=args.workers)
    manifest["finished_at"] = time.time()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
