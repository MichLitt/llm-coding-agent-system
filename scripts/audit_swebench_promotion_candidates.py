"""Audit frozen SWE-bench promotion candidates in clean temporary checkouts.

The script is intentionally separate from the accepted SWE runner.  A result
only establishes environment/replay evidence; promotion still requires review
of the generated override and pass-to-pass coverage.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SWEBENCH_ROOT = ROOT / "coder_agent" / "eval" / "benchmarks" / "swebench"
DEFAULT_SOURCE = SWEBENCH_ROOT / "promotion_candidates.source.json"
DEFAULT_OVERRIDES = SWEBENCH_ROOT / "promotion_candidates.overrides.json"


def _run(command: list[str] | str, *, cwd: Path, timeout: int = 300) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        shell=isinstance(command, str),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {"returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}


def _write_patch(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _workspace_test_command(command: str) -> str:
    if command.startswith("python "):
        return ".swebench-venv/bin/python " + command[len("python ") :]
    return command


def _batched(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def audit_candidate(
    task: dict[str, Any],
    override: dict[str, Any],
    *,
    repeats: int,
    include_pass_to_pass: bool,
    pass_to_pass_batch_size: int,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for repeat in range(1, repeats + 1):
        with tempfile.TemporaryDirectory(prefix=f"swe-audit-{task['instance_id']}-{repeat}-") as temp_dir:
            root = Path(temp_dir)
            workspace = root / "repo"
            attempt: dict[str, Any] = {"repeat": repeat, "stages": {}}
            clone = _run(["git", "clone", "-q", f"https://github.com/{task['repo']}.git", str(workspace)], cwd=root)
            attempt["stages"]["clone"] = clone
            if clone["returncode"]:
                attempts.append(attempt)
                continue
            checkout = _run(["git", "checkout", "-q", task["base_commit"]], cwd=workspace)
            attempt["stages"]["checkout"] = checkout
            if checkout["returncode"]:
                attempts.append(attempt)
                continue
            venv = _run(["uv", "venv", "--python", str(override["python_version"]), ".swebench-venv"], cwd=workspace)
            attempt["stages"]["venv"] = venv
            if venv["returncode"]:
                attempts.append(attempt)
                continue
            setup = [_run(command, cwd=workspace) for command in override["setup_commands"]]
            attempt["stages"]["setup"] = setup
            if any(step["returncode"] for step in setup):
                attempts.append(attempt)
                continue
            _write_patch(workspace / "test.patch", task["test_patch"])
            _write_patch(workspace / "gold.patch", task["patch"] + "\n" + task["test_patch"])
            apply_test = _run(["git", "apply", "--whitespace=nowarn", "test.patch"], cwd=workspace)
            test_command = _workspace_test_command(override["test_command"])
            buggy = _run(test_command, cwd=workspace)
            attempt["stages"]["buggy_apply"] = apply_test
            attempt["stages"]["buggy_test"] = buggy
            if apply_test["returncode"] or buggy["returncode"] == 0:
                attempts.append(attempt)
                continue
            revert = _run(["git", "apply", "-R", "--whitespace=nowarn", "test.patch"], cwd=workspace)
            apply_gold = _run(["git", "apply", "--whitespace=nowarn", "gold.patch"], cwd=workspace)
            gold = _run(test_command, cwd=workspace)
            attempt["stages"]["revert_test"] = revert
            attempt["stages"]["gold_apply"] = apply_gold
            attempt["stages"]["gold_test"] = gold
            if not include_pass_to_pass or gold["returncode"]:
                attempts.append(attempt)
                continue
            pass_to_pass = [str(value) for value in task.get("PASS_TO_PASS", []) if str(value).strip()]
            p2p_batches: list[dict[str, Any]] = []
            for batch in _batched(pass_to_pass, pass_to_pass_batch_size):
                command = test_command + " " + " ".join(shlex.quote(test_id) for test_id in batch)
                outcome = _run(command, cwd=workspace, timeout=600)
                p2p_batches.append({"count": len(batch), "returncode": outcome["returncode"], "stdout": outcome["stdout"], "stderr": outcome["stderr"]})
                if outcome["returncode"]:
                    break
            attempt["stages"]["pass_to_pass"] = p2p_batches
            attempts.append(attempt)
    passed = all(
        item["stages"].get("buggy_test", {}).get("returncode") not in (None, 0)
        and item["stages"].get("gold_test", {}).get("returncode") == 0
        and all(batch["returncode"] == 0 for batch in item["stages"].get("pass_to_pass", []))
        for item in attempts
    ) and len(attempts) == repeats
    return {"instance_id": task["instance_id"], "repeats": attempts, "passed": passed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--task-id", action="append", dest="task_ids")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--include-pass-to-pass", action="store_true")
    parser.add_argument("--pass-to-pass-batch-size", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    overrides = json.loads(args.overrides.read_text(encoding="utf-8"))["candidates"]
    tasks = [task for task in source["tasks"] if not args.task_ids or task["instance_id"] in args.task_ids]
    unknown = [task_id for task_id in (args.task_ids or []) if task_id not in {task["instance_id"] for task in tasks}]
    if unknown:
        raise SystemExit(f"unknown candidate task ID(s): {', '.join(unknown)}")
    if args.pass_to_pass_batch_size < 1:
        raise SystemExit("--pass-to-pass-batch-size must be positive")
    results = [
        audit_candidate(
            task,
            overrides[task["instance_id"]],
            repeats=args.repeats,
            include_pass_to_pass=args.include_pass_to_pass,
            pass_to_pass_batch_size=args.pass_to_pass_batch_size,
        )
        for task in tasks
    ]
    payload = {
        "schema_version": "swebench-candidate-audit/v1",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_revision": source["source_revision"],
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
