import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from coder_agent.core.agent import VerificationResult


def expected_checks(task: Any) -> int:
    if task.metadata.get("benchmark") in ("humaneval", "mbpp", "swebench"):
        return 1
    return max(1, len(task.verification))


def normalize_command(cmd: str) -> str:
    if cmd.startswith("python "):
        return f'"{sys.executable}" {cmd[len("python "):]}'
    if cmd.startswith("pytest "):
        return f'"{sys.executable}" -m {cmd}'
    return cmd


def run_check(check: dict[str, Any], workspace: Path) -> tuple[bool, str, str]:
    cmd = check.get("cmd", "")
    expect_exit = check.get("expect_exit", 0)
    if not cmd:
        return True, "", ""
    normalized_cmd = normalize_command(cmd)
    try:
        result = subprocess.run(
            normalized_cmd,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == expect_exit, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "TimeoutExpired"
    except Exception as exc:
        return False, "", str(exc)


def run_custom_checks(checks: list[dict[str, Any]], workspace: Path) -> int:
    checks_passed = 0
    for check in checks:
        ok, _, _ = run_check(check, workspace)
        if ok:
            checks_passed += 1
    return checks_passed


def verify_custom(checks: list[dict[str, Any]], workspace: Path) -> VerificationResult:
    failures: list[str] = []
    passed = 0
    for check in checks:
        ok, stdout, stderr = run_check(check, workspace)
        if ok:
            passed += 1
            continue
        cmd = check.get("cmd", "")
        signal = (stderr or stdout or "verification command failed").strip().splitlines()[0]
        failures.append(f"{cmd}: {signal}")

    if passed == len(checks):
        return VerificationResult(True, "External verification passed.")

    failure_summary = "; ".join(failures[:3])
    return VerificationResult(
        False,
        f"External verification failed ({passed}/{len(checks)} checks passed). {failure_summary}",
    )


def run_humaneval_check(task: Any, workspace: Path) -> tuple[int, str | None]:
    from coder_agent.eval.benchmarks.humaneval import HumanEvalBenchmark, HumanEvalTask

    metadata = task.metadata
    prompt = metadata.get("prompt", "")
    entry_point = metadata.get("entry_point", "")
    test = metadata.get("test", "")
    if not prompt or not entry_point or not test:
        return 0, "HumanEval metadata missing"

    benchmark = HumanEvalBenchmark()
    solution = benchmark.extract_solution_from_workspace(workspace, entry_point)
    if not solution.strip():
        return 0, "solution.py not created"

    result = benchmark.evaluate_solution(
        HumanEvalTask(
            task_id=task.task_id,
            prompt=prompt,
            entry_point=entry_point,
            test=test,
            canonical_solution="",
        ),
        solution,
    )
    if result.passed:
        return 1, None
    return 0, result.error or "HumanEval verification failed"


def verify_humaneval(task: Any, workspace: Path) -> VerificationResult:
    checks_passed, error_message = run_humaneval_check(task, workspace)
    if checks_passed == 1:
        return VerificationResult(True, "Official HumanEval verification passed.")
    summary = (error_message or "HumanEval verification failed").strip()
    summary = summary.splitlines()[0] if summary else "HumanEval verification failed"
    return VerificationResult(False, f"Official HumanEval verification failed. {summary}")


def run_mbpp_check(task: Any, workspace: Path) -> tuple[int, str | None]:
    from coder_agent.eval.benchmarks.mbpp import MBPPBenchmark

    solution_path = workspace / "solution.py"
    if not solution_path.exists():
        return 0, "solution.py not found"

    solution = solution_path.read_text(encoding="utf-8", errors="replace").strip()
    if not solution:
        return 0, "solution.py is empty"

    benchmark = MBPPBenchmark()
    passed = benchmark.evaluate_solution_from_metadata(task.metadata, solution)
    if passed:
        return 1, None
    return 0, "MBPP assertion tests failed"


def verify_mbpp(task: Any, workspace: Path) -> VerificationResult:
    checks_passed, error_message = run_mbpp_check(task, workspace)
    if checks_passed == 1:
        return VerificationResult(True, "Official MBPP verification passed.")
    summary = (error_message or "MBPP verification failed").strip()
    return VerificationResult(False, f"Official MBPP verification failed. {summary}")


def run_swebench_check(task: Any, workspace: Path) -> tuple[int, str | None]:
    from coder_agent.eval.benchmarks.swebench.adapter import (
        extract_patch,
        run_swebench_test_command,
        verification_overlay,
        write_patch_artifact,
    )

    patch_text = extract_patch(workspace)
    write_patch_artifact(workspace, patch_text)

    test_command = str(task.metadata.get("test_command", "")).strip()
    if not test_command:
        return 0, "SWE-bench metadata missing test_command"

    fail_to_pass = [str(item) for item in task.metadata.get("fail_to_pass", []) if str(item).strip()]
    pass_to_pass = [str(item) for item in task.metadata.get("pass_to_pass", []) if str(item).strip()]
    test_patch = str(task.metadata.get("test_patch", "") or "")
    replaceable_overlay_paths = {
        str(path).strip()
        for path in (
            list(task.metadata.get("authorized_test_edit_paths", []) or [])
            + list(task.metadata.get("verification_files", []) or [])
        )
        if str(path).strip()
    }

    try:
        with verification_overlay(
            workspace,
            test_patch=test_patch,
            replaceable_paths=replaceable_overlay_paths,
        ):
            suite_passed, suite_message = run_swebench_test_command(test_command, workspace)

            if not fail_to_pass and not pass_to_pass:
                if suite_passed:
                    return 1, None
                return 0, suite_message or "SWE-bench verification failed"

            missing_fail_to_pass: list[str] = []
            broken_pass_to_pass: list[str] = []
            for test_id in fail_to_pass:
                passed, _ = run_swebench_test_command(test_command, workspace, test_targets=[test_id])
                if not passed:
                    missing_fail_to_pass.append(test_id)
            for test_id in pass_to_pass:
                passed, _ = run_swebench_test_command(test_command, workspace, test_targets=[test_id])
                if not passed:
                    broken_pass_to_pass.append(test_id)

            if suite_passed and not missing_fail_to_pass and not broken_pass_to_pass:
                return 1, None

            reasons: list[str] = []
            if not suite_passed:
                reasons.append(f"full test command failed: {suite_message or 'non-zero exit'}")
            if missing_fail_to_pass:
                reasons.append("fail_to_pass still failing: " + ", ".join(missing_fail_to_pass[:3]))
            if broken_pass_to_pass:
                reasons.append("pass_to_pass regressed: " + ", ".join(broken_pass_to_pass[:3]))
            return 0, "; ".join(reasons) or "SWE-bench verification failed"
    except RuntimeError as exc:
        return 0, str(exc)


def verify_swebench(task: Any, workspace: Path) -> VerificationResult:
    checks_passed, error_message = run_swebench_check(task, workspace)
    if checks_passed == 1:
        return VerificationResult(True, "SWE-bench verification passed.")
    summary = (error_message or "SWE-bench verification failed").strip()
    summary = summary.splitlines()[0] if summary else "SWE-bench verification failed"
    return VerificationResult(False, f"SWE-bench verification failed. {summary}")


def build_verification_hook(task: Any, workspace: Path) -> Callable[[], VerificationResult] | None:
    contract = dict(task.verification_contract)
    if not contract:
        if task.metadata.get("benchmark") == "humaneval":
            contract = {"mode": "humaneval_official", "max_attempts": 2}
        elif task.metadata.get("benchmark") == "mbpp":
            contract = {"mode": "mbpp_official", "max_attempts": 2}
        elif task.verification:
            contract = {"mode": "custom_commands", "max_attempts": 2}
        else:
            return None

    mode = contract.get("mode")
    if mode == "humaneval_official":
        return lambda: verify_humaneval(task, workspace)
    if mode == "mbpp_official":
        return lambda: verify_mbpp(task, workspace)
    if mode == "swebench_patch_and_test":
        return lambda: verify_swebench(task, workspace)
    if mode == "custom_commands":
        return lambda: verify_custom(task.verification, workspace)
    return None


def finalize_trajectory(
    *,
    agent: Any,
    turn_result: Any,
    final_status: str,
    checks_passed: int,
    checks_total: int,
    duration: float,
) -> None:
    trajectory_id = getattr(turn_result, "trajectory_id", None)
    store = getattr(agent, "trajectory_store", None)
    if not trajectory_id or store is None:
        return

    partial_score = checks_passed / checks_total if checks_total else 0.0
    store.finish_trajectory(
        trajectory_id,
        final_status=final_status,
        termination_reason=getattr(turn_result, "termination_reason", None),
        partial_score=partial_score,
        total_tokens=getattr(turn_result, "total_tokens", 0),
        duration=duration,
    )
