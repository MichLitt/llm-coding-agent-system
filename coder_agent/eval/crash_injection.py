"""Crash-injection harness for checkpoint/resume correctness.

A child process runs a real ``Agent`` (real loop, run-state store, file tool and
``LLMClient`` usage accounting) against a deterministic scripted backend, and
dies abruptly at a chosen point:

  llm:K         inside the step-K model call, before it returns
  tool:K        after step K's file write, before its checkpoint is written
  checkpoint:K  right after step K's checkpoint commits
  (sigkill)     the driver sends a real SIGKILL at a random moment

``os._exit`` skips ``finally`` blocks, atexit hooks and buffered cleanup, so for
the store it is equivalent to an external kill -9. The driver then resumes the
run in a fresh process and compares it with an uninterrupted reference run.

Run a campaign:
    uv run python -m coder_agent.eval.crash_injection --runs 50 --output <path>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

CRASH_EXIT_CODE = 137
TASK = "Write step files one per step, then report done."
_RESULT_PREFIX = "RESULT "
_READY_LINE = "READY"


# ---------------------------------------------------------------------------
# Child process
# ---------------------------------------------------------------------------

class _ScriptedBackend:
    """Decides the next action from the conversation alone, like a real model.

    Because the step number is derived from the assistant turns already in the
    history, a correctly restored history makes the resumed run continue at
    the right step without any counters surviving the crash.
    """

    def __init__(self, steps: int, crash_step: int | None, delay: float) -> None:
        self.steps = steps
        self.crash_step = crash_step
        self.delay = delay

    async def chat(self, *, messages: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
        step = 1 + sum(1 for message in messages if message.get("role") == "assistant")
        if self.delay:
            time.sleep(self.delay)
        if step == self.crash_step:
            os._exit(CRASH_EXIT_CODE)
        usage = {"input_tokens": 100 * step, "output_tokens": 10, "cached_input_tokens": 0}
        if step > self.steps:
            return {"content": [{"type": "text", "text": "done"}], "tool_uses": [], "parse_errors": [], "usage": usage}
        call = {
            "id": f"call_{step}",
            "name": "write_file",
            "input": {"operation": "write", "path": f"step{step}.txt", "content": f"content {step}\n"},
        }
        return {
            "content": [{"type": "text", "text": f"Writing step {step}."}],
            "tool_uses": [call],
            "parse_errors": [],
            "usage": usage,
        }

    async def aclose(self) -> None:
        return None


def _parse_crash_at(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    kind, _, step = value.partition(":")
    if kind not in {"llm", "tool", "checkpoint"} or not step.isdigit():
        raise ValueError(f"invalid --crash-at {value!r}")
    return kind, int(step)


def _child(args: argparse.Namespace) -> int:
    from coder_agent.config import LLMProfile
    from coder_agent.core.agent import Agent
    from coder_agent.core.agent_types import ModelConfig
    from coder_agent.core.llm_client import LLMClient
    from coder_agent.memory.run_state import RunStateStore
    from coder_agent.tools.file_tools import WriteFileTool

    kind, crash_step = _parse_crash_at(args.crash_at)
    workspace = Path(args.workspace)
    store = RunStateStore(Path(args.db))

    if kind == "checkpoint":
        record_step = store.record_step

        def record_then_crash(run_id: str, step_index: int, **kwargs: Any) -> None:
            record_step(run_id, step_index, **kwargs)
            if step_index == crash_step - 1:
                os._exit(CRASH_EXIT_CODE)

        store.record_step = record_then_crash  # type: ignore[method-assign]

    tool = WriteFileTool(workspace)
    if kind == "tool":
        execute = tool.execute

        async def execute_then_crash(**kwargs: Any) -> str:
            result = await execute(**kwargs)
            if kwargs.get("path") == f"step{crash_step}.txt":
                os._exit(CRASH_EXIT_CODE)
            return result

        tool.execute = execute_then_crash  # type: ignore[method-assign]

    client = LLMClient(
        profile=LLMProfile(name="crash-harness", transport="openai", model="scripted", api_key="unused", base_url=None)
    )
    client._backend = _ScriptedBackend(args.steps, crash_step if kind == "llm" else None, args.delay)
    agent = Agent(
        tools=[tool],
        client=client,
        model_config=ModelConfig(),
        run_state_store=store,
        workspace=workspace,
        verbose=False,
    )
    print(_READY_LINE, flush=True)
    result = agent.run(
        TASK,
        run_id=args.run_id,
        resume=args.resume,
        record_memory=False,
        max_steps=args.steps + 2,
    )
    print(_RESULT_PREFIX + json.dumps({"resume_mode": result.extra.get("resume_mode")}), flush=True)
    return 0


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _spawn(
    *,
    db: Path,
    workspace: Path,
    run_id: str,
    steps: int,
    resume: bool = False,
    crash_at: str | None = None,
    delay: float = 0.0,
) -> subprocess.Popen[str]:
    cmd = [
        sys.executable, "-m", "coder_agent.eval.crash_injection", "child",
        "--db", str(db), "--workspace", str(workspace), "--run-id", run_id,
        "--steps", str(steps), "--delay", str(delay),
    ]
    if resume:
        cmd.append("--resume")
    if crash_at:
        cmd += ["--crash-at", crash_at]
    project_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(project_root), os.environ.get("PYTHONPATH")]))}
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=project_root)


def _finish(proc: subprocess.Popen[str], timeout: float = 120.0) -> tuple[int, str, str]:
    stdout, stderr = proc.communicate(timeout=timeout)
    return proc.returncode, stdout, stderr


def _resume_mode(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if line.startswith(_RESULT_PREFIX):
            return json.loads(line[len(_RESULT_PREFIX):]).get("resume_mode")
    return None


def _run_status(db: Path, run_id: str) -> str | None:
    from coder_agent.memory.run_state import RunStateStore

    if not db.exists():
        return None
    store = RunStateStore(db)
    try:
        run = store.get_run(run_id)
    finally:
        store.close()
    return None if run is None else str(run.get("status"))


def _observe(db: Path, workspace: Path, run_id: str) -> dict[str, Any]:
    """Final persisted state of a run, read from the store and the workspace."""
    from coder_agent.core.checkpoint import decode_history_snapshot
    from coder_agent.memory.run_state import RunStateStore

    store = RunStateStore(db)
    try:
        run = store.get_run(run_id) or {}
        final = store.latest_resumable_checkpoint(run_id)
        snapshot = decode_history_snapshot(final.get("history_snapshot")) if final else None
        messages = snapshot["messages"] if snapshot else None
        steps = [step["step_index"] for step in store.list_steps(run_id)]
    finally:
        store.close()
    files = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(workspace.glob("step*.txt"))
    }
    history_sha = (
        hashlib.sha256(json.dumps(messages, sort_keys=True).encode("utf-8")).hexdigest() if messages else None
    )
    return {
        "status": run.get("status"),
        "total_steps": run.get("total_steps"),
        "total_tokens": run.get("total_tokens"),
        "llm_calls": run.get("llm_calls"),
        "step_indices": steps,
        "files": files,
        "history_sha256": history_sha,
        "drift_notices": sum(
            1 for message in messages or [] if str(message.get("content", "")).startswith("[Resume notice]")
        ),
    }


def run_reference(base: Path, *, steps: int) -> dict[str, Any]:
    workspace = base / "workspace"
    workspace.mkdir(parents=True)
    code, _stdout, stderr = _finish(_spawn(db=base / "run_state.db", workspace=workspace, run_id="ref", steps=steps))
    if code != 0:
        raise RuntimeError(f"reference run failed ({code}): {stderr[-2000:]}")
    return _observe(base / "run_state.db", workspace, "ref")


def run_case(
    base: Path,
    *,
    steps: int,
    reference: dict[str, Any],
    crash_at: str | None = None,
    sigkill_after: float | None = None,
    delay: float = 0.0,
) -> dict[str, Any]:
    """Crash one run, resume it once in a new process, and compare with the reference."""
    db, workspace, run_id = base / "run_state.db", base / "workspace", "case"
    workspace.mkdir(parents=True)
    proc = _spawn(db=db, workspace=workspace, run_id=run_id, steps=steps, crash_at=crash_at, delay=delay)
    if sigkill_after is not None:
        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if not line or line.strip() == _READY_LINE:
                break
        time.sleep(sigkill_after)
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
    first_code, _stdout, first_err = _finish(proc)
    crashed = first_code != 0
    resume_mode = None
    second_code = None
    second_err = ""
    status = _run_status(db, run_id) if crashed else None
    # A SIGKILL can land before the run row exists (nothing persisted: start
    # over) or after the run already finished (nothing left to resume).
    if crashed and status != "success":
        second_code, second_out, second_err = _finish(
            _spawn(db=db, workspace=workspace, run_id=run_id, steps=steps, resume=status is not None)
        )
        resume_mode = _resume_mode(second_out)
    observed = _observe(db, workspace, run_id)
    drift = resume_mode in ("exact_workspace_drift", "restart_workspace_drift")
    checks = {
        "completed": observed["status"] == "success" and (second_code in (None, 0)),
        "files_equal": observed["files"] == reference["files"],
        "steps_equal": observed["total_steps"] == reference["total_steps"],
        "step_rows_contiguous": observed["step_indices"] == list(range(len(observed["step_indices"])))
        and len(observed["step_indices"]) == len(reference["step_indices"]),
        "usage_equal": observed["total_tokens"] == reference["total_tokens"]
        and observed["llm_calls"] == reference["llm_calls"],
        # Exact resumes and clean restarts must reproduce the uninterrupted
        # history byte for byte; drift resumes differ only by the one notice
        # they add. Summary resumes are inexact and fail this check.
        "history_ok": observed["drift_notices"] == 1
        if drift
        else observed["history_sha256"] == reference["history_sha256"],
    }
    return {
        "crash_at": crash_at,
        "sigkill_after_seconds": sigkill_after,
        "crashed": crashed,
        "first_exit_code": first_code,
        "resume_mode": resume_mode,
        "passed": all(checks.values()),
        "checks": checks,
        "stderr_tail": (second_err or first_err)[-500:] if not all(checks.values()) else "",
    }


def run_campaign(*, runs: int, steps: int, seed: int, delay: float) -> dict[str, Any]:
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="crash-injection-") as tmp:
        root = Path(tmp)
        reference = run_reference(root / "reference", steps=steps)
        cases = []
        for index in range(runs):
            mode = rng.choice(["llm", "tool", "checkpoint", "sigkill"])
            base = root / f"case-{index:03d}"
            if mode == "sigkill":
                after = round(rng.uniform(0.0, (steps + 1) * delay), 3)
                case = run_case(base, steps=steps, reference=reference, sigkill_after=after, delay=delay)
            else:
                case = run_case(base, steps=steps, reference=reference, crash_at=f"{mode}:{rng.randint(1, steps)}")
            case["mode"] = mode
            cases.append(case)
    by_mode: dict[str, dict[str, int]] = {}
    for case in cases:
        bucket = by_mode.setdefault(case["mode"], {"runs": 0, "crashed": 0, "passed": 0})
        bucket["runs"] += 1
        bucket["crashed"] += int(case["crashed"])
        bucket["passed"] += int(case["passed"])
    resume_modes: dict[str, int] = {}
    for case in cases:
        key = str(case["resume_mode"])
        resume_modes[key] = resume_modes.get(key, 0) + 1
    return {
        "schema_version": "crash-injection/v1",
        "seed": seed,
        "steps_per_run": steps,
        "sigkill_call_delay_seconds": delay,
        "runs": len(cases),
        "crashed_runs": sum(case["crashed"] for case in cases),
        "passed_runs": sum(case["passed"] for case in cases),
        "by_mode": by_mode,
        "resume_modes": resume_modes,
        "reference": {key: reference[key] for key in ("total_steps", "total_tokens", "llm_calls", "history_sha256")},
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    child = sub.add_parser("child")
    child.add_argument("--db", required=True)
    child.add_argument("--workspace", required=True)
    child.add_argument("--run-id", required=True)
    child.add_argument("--steps", type=int, default=5)
    child.add_argument("--delay", type=float, default=0.0)
    child.add_argument("--resume", action="store_true")
    child.add_argument("--crash-at")
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--delay", type=float, default=0.05, help="per-call delay for SIGKILL runs")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "child":
        return _child(args)
    started = time.time()
    report = run_campaign(runs=args.runs, steps=args.steps, seed=args.seed, delay=args.delay)
    report["duration_seconds"] = round(time.time() - started, 1)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("runs", "crashed_runs", "passed_runs", "by_mode", "resume_modes")}))
    return 0 if report["passed_runs"] == report["runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
