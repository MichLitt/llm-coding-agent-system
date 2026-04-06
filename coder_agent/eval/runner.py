"""Eval runner public facade."""

import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from coder_agent.config import cfg, resolve_llm_profile
from coder_agent.eval.eval_checkpoint import (
    append_checkpoint_result,
    clear_run_artifacts,
    load_checkpoint_results,
    read_manifest,
    result_paths,
    write_results_json,
    write_run_manifest,
)
from coder_agent.eval.eval_compare import write_comparison_report
from coder_agent.eval.eval_verification import (
    build_verification_hook,
    expected_checks,
    finalize_trajectory,
    normalize_command,
    run_check,
    run_custom_checks,
    run_humaneval_check,
    run_mbpp_check,
    verify_custom,
    verify_humaneval,
)
from coder_agent.eval.eval_workspace import prepare_workspace
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
    verification_contract: dict[str, Any] = field(default_factory=dict)
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
        llm_profile_name: str | None = None,
    ):
        self.agent_factory = agent_factory
        self.output_dir = output_dir or cfg.eval.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.trajectory_dir = trajectory_dir or cfg.eval.trajectory_dir
        # Resolve LLM profile for manifest auditing
        _profile = resolve_llm_profile(llm_profile_name)
        self._llm_profile_name: str = _profile.name
        self._llm_model: str = _profile.model
        self._llm_transport: str = _profile.transport

    def _result_paths(self, config_label: str) -> tuple[Path, Path, Path]:
        return result_paths(self.output_dir, config_label)

    def _clear_run_artifacts(self, config_label: str) -> None:
        clear_run_artifacts(self.output_dir, config_label)

    def _load_checkpoint_results(self, config_label: str) -> list[EvalResult]:
        return load_checkpoint_results(self.output_dir, config_label)

    def _append_checkpoint_result(self, config_label: str, result: EvalResult) -> None:
        append_checkpoint_result(self.output_dir, config_label, result)

    def _write_results_json(self, config_label: str, results: list[EvalResult]) -> None:
        write_results_json(self.output_dir, config_label, results)

    def _read_manifest(self, config_label: str) -> dict[str, Any]:
        return read_manifest(self.output_dir, config_label)

    def _write_run_manifest(
        self,
        config_label: str,
        *,
        benchmark_name: str,
        preset: str,
        agent_config: dict[str, Any] | None,
        experiment_config: dict[str, Any] | None,
        total_tasks: int,
        results: list[EvalResult],
        resume_enabled: bool,
        started_at: float,
        finished_at: float | None,
    ) -> None:
        write_run_manifest(
            self.output_dir,
            config_label,
            benchmark_name=benchmark_name,
            preset=preset,
            agent_config_snapshot=agent_config or {},
            runtime_experiment_config_snapshot=experiment_config or {},
            total_tasks=total_tasks,
            results=results,
            resume_enabled=resume_enabled,
            started_at=started_at,
            finished_at=finished_at,
            llm_profile_name=self._llm_profile_name,
            llm_model=self._llm_model,
            llm_transport=self._llm_transport,
        )

    def run_task(
        self,
        task: TaskSpec,
        agent: Any,
        config_label: str = "",
    ) -> EvalResult:
        workspace = cfg.agent.workspace
        prepare_workspace(task.setup_files, workspace)
        verification_hook = build_verification_hook(task, workspace)
        gate_enabled = bool(getattr(agent, "experiment_config", {}).get("verification_gate", False))

        start = time.time()
        try:
            turn_result = agent.run(
                task.description,
                task_id=task.task_id,
                finalize_trajectory=False,
                verification_hook=verification_hook,
                max_verification_attempts=task.verification_contract.get("max_attempts", 2),
                enforce_stop_verification=(verification_hook is not None),
                auto_complete_on_verification=verification_hook is not None,
                max_steps=task.max_steps,
            )
        except Exception as exc:
            tb_summary = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
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
                error_types=[
                    f"Exception class: {type(exc).__name__}",
                    f"Message: {exc}",
                    f"Traceback:\n{tb_summary[:1200]}",
                ],
                activation_counters={},
                config_label=config_label,
            )

        checks_total = self._expected_checks(task)
        error_types: list[str] = []
        if task.metadata.get("benchmark") == "humaneval":
            checks_passed, error_message = self._run_humaneval_check(task, workspace)
            if error_message:
                error_types.append(error_message)
        elif task.metadata.get("benchmark") == "mbpp":
            checks_passed, error_message = run_mbpp_check(task, workspace)
            if error_message:
                error_types.append(error_message)
        else:
            checks_passed = self._run_custom_checks(task.verification, workspace)
        if getattr(turn_result, "error_details", None):
            error_types.extend(
                detail for detail in turn_result.error_details if detail and detail not in error_types
            )

        verification_pass_rate = checks_passed / checks_total if checks_total else 0.0
        benchmark_passed = checks_passed == checks_total
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
            activation_counters=dict(getattr(turn_result, "extra", {}) or {}),
            config_label=config_label,
        )

    def run_suite(
        self,
        tasks: list[TaskSpec],
        config_label: str = "",
        agent_config: dict | None = None,
        experiment_config: dict | None = None,
        benchmark_name: str = "",
        preset: str = "default",
        resume: bool = False,
        verbose: bool = True,
    ) -> list[EvalResult]:
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
            agent_config=agent_config,
            experiment_config=experiment_config,
            total_tasks=len(tasks),
            results=results,
            resume_enabled=resume,
            started_at=started_at,
            finished_at=None,
        )

        completed_task_ids = {result.task_id for result in results}
        agent = self.agent_factory(agent_config or {})

        try:
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
                    agent_config=agent_config,
                    experiment_config=experiment_config,
                    total_tasks=len(tasks),
                    results=results,
                    resume_enabled=resume,
                    started_at=started_at,
                    finished_at=None,
                )
                if verbose:
                    status = "OK" if result.success else "ERR"
                    print(f"  {status} checks={result.checks_passed}/{result.checks_total} steps={result.steps_used}")

            self._write_results_json(config_label, results)
            self._write_run_manifest(
                config_label,
                benchmark_name=benchmark_name,
                preset=preset,
                agent_config=agent_config,
                experiment_config=experiment_config,
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
        finally:
            if hasattr(agent, "close"):
                agent.close()

    def compare_configs(
        self,
        tasks: list[TaskSpec],
        configs: dict[str, dict],
        report_label: str = "",
        experiment_config: dict | None = None,
        benchmark_name: str = "",
        resume: bool = False,
        verbose: bool = True,
    ) -> ComparisonReport:
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
                experiment_config=experiment_config,
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

        write_comparison_report(
            self.output_dir,
            report_label=report_label,
            configs=configs,
            summaries=all_summaries,
        )

        return ComparisonReport(
            configs=list(configs.keys()),
            summaries=all_summaries,
            raw_results=all_results,
        )

    def _expected_checks(self, task: TaskSpec) -> int:
        return expected_checks(task)

    def _run_custom_checks(self, checks: list[dict[str, Any]], workspace: Path) -> int:
        return run_custom_checks(checks, workspace)

    def _build_verification_hook(self, task: TaskSpec, workspace: Path):
        return build_verification_hook(task, workspace)

    def _verify_custom(self, checks: list[dict[str, Any]], workspace: Path):
        return verify_custom(checks, workspace)

    def _run_humaneval_check(self, task: TaskSpec, workspace: Path):
        return run_humaneval_check(task, workspace)

    def _verify_humaneval(self, task: TaskSpec, workspace: Path):
        return verify_humaneval(task, workspace)

    def _finalize_trajectory(
        self,
        agent: Any,
        turn_result: Any,
        final_status: str,
        checks_passed: int,
        checks_total: int,
        duration: float,
    ) -> None:
        finalize_trajectory(
            agent=agent,
            turn_result=turn_result,
            final_status=final_status,
            checks_passed=checks_passed,
            checks_total=checks_total,
            duration=duration,
        )

    def _run_check(self, check: dict[str, Any], workspace: Path) -> tuple[bool, str, str]:
        return run_check(check, workspace)

    def _normalize_command(self, cmd: str) -> str:
        return normalize_command(cmd)
