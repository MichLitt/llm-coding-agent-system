"""Eval runner for custom tasks and HumanEval."""

import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from coder_agent.config import cfg
from coder_agent.eval.metrics import (
    EvalResult,
    MetricsSummary,
    compute_metrics,
    print_metrics_table,
)


@dataclass
class TaskSpec:
    """Generic task specification for benchmark execution."""

    task_id: str
    description: str
    difficulty: str = "medium"
    setup_files: list[str] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    max_steps: int = 15
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparisonReport:
    """Results from a multi-config comparison run."""

    configs: list[str]
    summaries: dict[str, MetricsSummary]
    raw_results: dict[str, list[EvalResult]]


class EvalRunner:
    """Run benchmark tasks and collect evaluation artifacts."""

    def __init__(
        self,
        agent_factory: Callable[[dict], Any],
        output_dir: Path | None = None,
        trajectory_dir: Path | None = None,
    ):
        self.agent_factory = agent_factory
        self.output_dir = output_dir or cfg.eval.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.trajectory_dir = trajectory_dir or cfg.eval.trajectory_dir

    def _result_paths(self, config_label: str) -> tuple[Path, Path, Path]:
        stem = config_label or "results"
        return (
            self.output_dir / f"{stem}.json",
            self.output_dir / f"{stem}.jsonl",
            self.output_dir / f"{stem}_run_manifest.json",
        )

    def _clear_run_artifacts(self, config_label: str) -> None:
        for path in self._result_paths(config_label):
            if path.exists():
                path.unlink()

    def _load_checkpoint_results(self, config_label: str) -> list[EvalResult]:
        _, checkpoint_path, _ = self._result_paths(config_label)
        if not checkpoint_path.exists():
            return []

        results: list[EvalResult] = []
        index_by_task_id: dict[str, int] = {}
        with checkpoint_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                result = EvalResult(**raw)
                existing_index = index_by_task_id.get(result.task_id)
                if existing_index is None:
                    index_by_task_id[result.task_id] = len(results)
                    results.append(result)
                else:
                    results[existing_index] = result
        return results

    def _append_checkpoint_result(self, config_label: str, result: EvalResult) -> None:
        _, checkpoint_path, _ = self._result_paths(config_label)
        with checkpoint_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")

    def _write_results_json(self, config_label: str, results: list[EvalResult]) -> None:
        out_path, _, _ = self._result_paths(config_label)
        with out_path.open("w", encoding="utf-8") as handle:
            json.dump([asdict(result) for result in results], handle, indent=2, ensure_ascii=False)

    def _read_manifest(self, config_label: str) -> dict[str, Any]:
        _, _, manifest_path = self._result_paths(config_label)
        if not manifest_path.exists():
            return {}
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def _write_run_manifest(
        self,
        config_label: str,
        *,
        benchmark_name: str,
        preset: str,
        total_tasks: int,
        results: list[EvalResult],
        resume_enabled: bool,
        started_at: float,
        finished_at: float | None,
    ) -> None:
        _, _, manifest_path = self._result_paths(config_label)
        manifest = {
            "config_label": config_label or "results",
            "benchmark": benchmark_name,
            "preset": preset,
            "git_commit": self._git_commit(),
            "started_at": started_at,
            "finished_at": finished_at,
            "completed_task_ids": [result.task_id for result in results],
            "total_tasks": total_tasks,
            "resume_enabled": resume_enabled,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _git_commit(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def run_task(
        self,
        task: TaskSpec,
        agent: Any,
        config_label: str = "",
    ) -> EvalResult:
        """Run a single task and return an EvalResult."""
        workspace = cfg.agent.workspace
        self._prepare_workspace(task, workspace)

        start = time.time()
        try:
            turn_result = agent.run(
                task.description,
                task_id=task.task_id,
                finalize_trajectory=False,
            )
        except Exception as exc:
            return EvalResult(
                task_id=task.task_id,
                success=False,
                benchmark_passed=False,
                agent_completed_cleanly=False,
                agent_final_status="failed",
                checks_passed=0,
                checks_total=self._expected_checks(task),
                steps_used=0,
                retry_steps=0,
                termination_reason="loop_exception",
                verification_pass_rate=0.0,
                duration=time.time() - start,
                error_types=[type(exc).__name__],
                config_label=config_label,
            )

        checks_passed = 0
        checks_total = self._expected_checks(task)
        error_types: list[str] = []

        if task.metadata.get("benchmark") == "humaneval":
            checks_passed, error_message = self._run_humaneval_check(task, workspace)
            if error_message:
                error_types.append(error_message)
        else:
            checks_passed = self._run_custom_checks(task.verification, workspace)

        verification_pass_rate = checks_passed / checks_total if checks_total else 0.0
        benchmark_passed = checks_passed == checks_total
        # Benchmark check is the external ground truth. If benchmark passed and the
        # agent did not time out, treat the task as cleanly completed even if the
        # agent's internal final_status was "failed" (e.g. due to a spurious tool
        # error after the solution was already written and verified).
        agent_completed_cleanly = turn_result.final_status == "success" or (
            benchmark_passed and turn_result.final_status != "timeout"
        )
        success = benchmark_passed and agent_completed_cleanly
        final_status = turn_result.final_status
        if final_status != "timeout":
            final_status = "success" if success else "failed"

        self._finalize_trajectory(
            agent=agent,
            turn_result=turn_result,
            final_status=final_status,
            checks_passed=checks_passed,
            checks_total=checks_total,
            duration=time.time() - start,
        )

        return EvalResult(
            task_id=task.task_id,
            success=success,
            benchmark_passed=benchmark_passed,
            agent_completed_cleanly=agent_completed_cleanly,
            agent_final_status=turn_result.final_status,
            checks_passed=checks_passed,
            checks_total=checks_total,
            steps_used=turn_result.steps,
            retry_steps=turn_result.retry_steps,
            termination_reason=getattr(turn_result, "termination_reason", None),
            verification_pass_rate=verification_pass_rate,
            total_tokens=turn_result.total_tokens,
            duration=time.time() - start,
            error_types=error_types,
            config_label=config_label,
        )

    def run_suite(
        self,
        tasks: list[TaskSpec],
        config_label: str = "",
        agent_config: dict | None = None,
        benchmark_name: str = "",
        preset: str = "default",
        resume: bool = False,
        verbose: bool = True,
    ) -> list[EvalResult]:
        """Run all tasks and return a list of EvalResult."""
        if resume:
            results = self._load_checkpoint_results(config_label)
            previous_manifest = self._read_manifest(config_label)
            started_at = previous_manifest.get("started_at", time.time())
        else:
            self._clear_run_artifacts(config_label)
            results = []
            started_at = time.time()

        self._write_results_json(config_label, results)
        self._write_run_manifest(
            config_label,
            benchmark_name=benchmark_name,
            preset=preset,
            total_tasks=len(tasks),
            results=results,
            resume_enabled=resume,
            started_at=started_at,
            finished_at=None,
        )

        completed_task_ids = {result.task_id for result in results}
        agent_config = agent_config or {}
        agent = self.agent_factory(agent_config)

        for index, task in enumerate(tasks, start=1):
            if task.task_id in completed_task_ids:
                if verbose:
                    print(f"\n[{index}/{len(tasks)}] Task: {task.task_id} ({task.difficulty})")
                    print("  SKIP from checkpoint")
                continue
            if hasattr(agent, "reset"):
                agent.reset()
            if verbose:
                print(f"\n[{index}/{len(tasks)}] Task: {task.task_id} ({task.difficulty})")
            result = self.run_task(task, agent, config_label=config_label)
            results.append(result)
            completed_task_ids.add(task.task_id)
            self._append_checkpoint_result(config_label, result)
            self._write_results_json(config_label, results)
            self._write_run_manifest(
                config_label,
                benchmark_name=benchmark_name,
                preset=preset,
                total_tasks=len(tasks),
                results=results,
                resume_enabled=resume,
                started_at=started_at,
                finished_at=None,
            )
            status = "OK" if result.success else "ERR"
            if verbose:
                print(
                    f"  {status} checks={result.checks_passed}/{result.checks_total} "
                    f"steps={result.steps_used}"
                )

        self._write_results_json(config_label, results)
        self._write_run_manifest(
            config_label,
            benchmark_name=benchmark_name,
            preset=preset,
            total_tasks=len(tasks),
            results=results,
            resume_enabled=resume,
            started_at=started_at,
            finished_at=time.time(),
        )

        if verbose:
            metrics = compute_metrics(results, config_label)
            print(f"\n=== {config_label or 'results'} Summary ===")
            print_metrics_table([metrics])

        return results

    def compare_configs(
        self,
        tasks: list[TaskSpec],
        configs: dict[str, dict],
        report_label: str = "",
        benchmark_name: str = "",
        resume: bool = False,
        verbose: bool = True,
    ) -> ComparisonReport:
        """Run the C1/C2/C3/C4 comparison matrix."""
        all_results: dict[str, list[EvalResult]] = {}
        all_summaries: dict[str, MetricsSummary] = {}

        for config_label, agent_config in configs.items():
            if verbose:
                print(f"\n{'=' * 50}")
                print(f"Running config: {config_label}")
                print(f"{'=' * 50}")
            results = self.run_suite(
                tasks,
                config_label=config_label,
                agent_config=agent_config,
                benchmark_name=benchmark_name,
                preset=config_label.rsplit("_", maxsplit=1)[-1],
                resume=resume,
                verbose=verbose,
            )
            all_results[config_label] = results
            all_summaries[config_label] = compute_metrics(results, config_label)

        if verbose:
            print(f"\n{'=' * 60}")
            print("=== COMPARISON RESULTS ===")
            print_metrics_table(list(all_summaries.values()))

        report_stem = f"{report_label}_comparison_report.json" if report_label else "comparison_report.json"
        report_path = self.output_dir / report_stem
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    label: asdict(summary)
                    for label, summary in all_summaries.items()
                },
                handle,
                indent=2,
            )

        if report_label:
            manifest_path = self.output_dir / f"{report_label}_comparison_manifest.json"
            with manifest_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "compare_label": report_label,
                        "experiments": list(configs.keys()),
                        "report_path": str(report_path),
                    },
                    handle,
                    indent=2,
                )

        return ComparisonReport(
            configs=list(configs.keys()),
            summaries=all_summaries,
            raw_results=all_results,
        )

    def _expected_checks(self, task: TaskSpec) -> int:
        if task.metadata.get("benchmark") == "humaneval":
            return 1
        return max(1, len(task.verification))

    def _prepare_workspace(self, task: TaskSpec, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        for child in workspace.iterdir():
            if child.name == ".gitkeep":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

        setup_dir = Path(__file__).parent / "benchmarks" / "custom" / "setup_files"
        for filename in task.setup_files:
            src = setup_dir / filename
            dst = workspace / filename
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    def _run_custom_checks(
        self,
        checks: list[dict[str, Any]],
        workspace: Path,
    ) -> int:
        checks_passed = 0
        for check in checks:
            if self._run_check(check, workspace):
                checks_passed += 1
        return checks_passed

    def _run_humaneval_check(
        self,
        task: TaskSpec,
        workspace: Path,
    ) -> tuple[int, str | None]:
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

    def _finalize_trajectory(
        self,
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
            partial_score=partial_score,
            total_tokens=getattr(turn_result, "total_tokens", 0),
            duration=duration,
        )

    def _run_check(self, check: dict[str, Any], workspace: Path) -> bool:
        """Run a single verification command."""
        cmd = check.get("cmd", "")
        expect_exit = check.get("expect_exit", 0)
        if not cmd:
            return True
        normalized_cmd = self._normalize_command(cmd)
        try:
            result = subprocess.run(
                normalized_cmd,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                timeout=30,
            )
            return result.returncode == expect_exit
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False

    def _normalize_command(self, cmd: str) -> str:
        """Run verification commands with the current interpreter when possible."""
        if cmd.startswith("python "):
            return f'"{sys.executable}" {cmd[len("python "):]}'
        if cmd.startswith("pytest "):
            return f'"{sys.executable}" -m {cmd}'
        return cmd
