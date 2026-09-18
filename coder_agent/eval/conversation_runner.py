"""Deterministic task-boundary runner for ConversationBench v1."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from coder_agent.eval.conversation_models import SCHEMA_VERSION, ConversationTaskSpec
from coder_agent.eval.eval_verification import run_check
from coder_agent.eval.eval_checkpoint import git_commit


def _sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass
class ConversationTurnResult:
    schema_version: str
    conversation_id: str
    turn_id: str
    user_message_sha256: str
    workspace_before_sha256: str
    workspace_after_sha256: str
    phase_checks: dict[str, int]
    constraint_results: dict[str, bool]
    steps: int
    tool_calls: int
    tokens: int
    duration_seconds: float
    agent_final_status: str
    termination_reason: str | None
    failure_categories: list[str]


@dataclass
class ConversationResult:
    conversation_id: str
    success: bool
    turns: list[ConversationTurnResult]
    final_checks: dict[str, int]
    workspace_final_sha256: str
    manifest_sha256: str
    failure_categories: list[str]
    total_turns: int


class ConversationRunner:
    """Runs one shared AgentSession and workspace for every conversation task."""

    def __init__(self, session_factory: Callable[[Path], Any], output_dir: Path, fixture_root: Path, *, run_metadata: dict[str, Any] | None = None):
        self.session_factory = session_factory
        self.output_dir = output_dir
        self.fixture_root = fixture_root
        self.run_metadata = dict(run_metadata or {})
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _prepare_workspace(self, task: ConversationTaskSpec, workspace: Path) -> None:
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True)
        for relative in task.setup_files:
            source = (self.fixture_root / relative).resolve()
            if self.fixture_root.resolve() not in source.parents or not source.is_file():
                raise ValueError(f"invalid conversation fixture path: {relative}")
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @staticmethod
    def _checks(checks: list[dict[str, Any]], workspace: Path) -> tuple[int, int]:
        return sum(1 for check in checks if run_check(check, workspace)[0]), len(checks)

    def run_task(self, task: ConversationTaskSpec, workspace: Path) -> ConversationResult:
        self._prepare_workspace(task, workspace)
        session = self.session_factory(workspace)
        turn_results: list[ConversationTurnResult] = []
        failures: list[str] = []
        total_steps = 0
        try:
            for turn in task.turns:
                before = _tree_sha256(workspace)
                start = time.time()
                try:
                    remaining = max(1, task.max_total_steps - total_steps)
                    result = session.send(turn.user_message, task_id=f"{task.conversation_id}:{turn.turn_id}", max_steps=min(turn.max_steps, remaining))
                    total_steps += result.steps
                    phase_passed, phase_total = self._checks(turn.phase_checks, workspace)
                    constraints = {
                        constraint: self._checks(task.verification_contract["constraint_checks"][constraint], workspace)[0]
                        == len(task.verification_contract["constraint_checks"][constraint])
                        for constraint in turn.introduces_constraints + turn.retains_constraints
                    }
                    categories = list(getattr(result, "error_details", []) or [])
                    if turn.expects_clarification:
                        if before != _tree_sha256(workspace):
                            categories.append("premature_action")
                        patterns = task.verification_contract.get("clarification_patterns", {}).get(turn.turn_id, [])
                        content = str(getattr(result, "content", "")).lower()
                        if patterns and not any(str(pattern).lower() in content for pattern in patterns):
                            categories.append("clarification_missing")
                    if phase_passed != phase_total:
                        categories.append("verification_failure")
                    if not all(constraints.values()):
                        categories.append("constraint_failure")
                except Exception as exc:
                    result, phase_passed, phase_total, constraints = None, 0, len(turn.phase_checks), {}
                    categories = [f"infra_exception:{type(exc).__name__}"]
                turn_result = ConversationTurnResult(
                    SCHEMA_VERSION, task.conversation_id, turn.turn_id, _sha256(turn.user_message), before,
                    _tree_sha256(workspace), {"passed": phase_passed, "total": phase_total}, constraints,
                    int(getattr(result, "steps", 0)), len(getattr(result, "tool_calls", []) or []),
                    int(getattr(result, "total_tokens", 0)), time.time() - start,
                    str(getattr(result, "final_status", "failed")), getattr(result, "termination_reason", None), categories,
                )
                turn_results.append(turn_result)
                failures.extend(categories)
                # A failed turn is recorded but never treated as a completed task.
                if categories:
                    break
            final_passed, final_total = self._checks(task.final_checks, workspace)
            success = len(turn_results) == len(task.turns) and not failures and final_passed == final_total
            outcome = ConversationResult(task.conversation_id, success, turn_results, {"passed": final_passed, "total": final_total}, _tree_sha256(workspace), _sha256(task.snapshot()), sorted(set(failures)), len(task.turns))
            self._append_artifacts(outcome)
            return outcome
        finally:
            if hasattr(session, "close"):
                session.close()

    def run_suite(self, tasks: list[ConversationTaskSpec], workspace_root: Path, *, resume: bool = False) -> list[ConversationResult]:
        """Resume only at a completed conversation boundary, never mid-session."""
        manifest_path = self.output_dir / "conversation_run_manifest.json"
        expected = {task.conversation_id: _sha256(task.snapshot()) for task in tasks}

        def write_manifest(results: list[ConversationResult]) -> None:
            """Checkpoint after each task so an interrupted suite can resume safely."""
            manifest_path.write_text(json.dumps({
                "schema_version": SCHEMA_VERSION, "task_manifest_sha256": _sha256(expected),
                "task_ids": list(expected),
                "completed_conversation_ids": [item.conversation_id for item in results if item.success],
                "resume_boundary": "conversation_task_only", "fixture_tree_sha256": _tree_sha256(self.fixture_root),
                "git_commit": git_commit(), "run_metadata": self.run_metadata,
                "results": [{"conversation_id": item.conversation_id, "success": item.success, "summary_sha256": _sha256(asdict(item))} for item in results],
            }, indent=2), encoding="utf-8")

        def load_completed_summary(conversation_id: str) -> ConversationResult | None:
            summary = self.output_dir / f"{conversation_id}_summary.json"
            if not summary.exists():
                return None
            raw = json.loads(summary.read_text(encoding="utf-8"))
            if raw.get("success") is not True or raw.get("manifest_sha256") != expected[conversation_id]:
                return None
            return ConversationResult(
                conversation_id, True, [], raw["final_checks"], raw["workspace_final_sha256"],
                raw["manifest_sha256"], raw["failure_categories"], raw["total_turns"],
            )

        if not resume:
            for path in [manifest_path, self.output_dir / "conversation_turns.jsonl"]:
                if path.exists(): path.unlink()
            for task in tasks:
                summary = self.output_dir / f"{task.conversation_id}_summary.json"
                if summary.exists(): summary.unlink()
        completed: dict[str, ConversationResult] = {}
        if resume and manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous.get("task_manifest_sha256") != _sha256(expected):
                raise ValueError("cannot resume: conversation task manifest changed")
            for conversation_id in previous.get("completed_conversation_ids", []):
                if conversation_id in expected:
                    result = load_completed_summary(conversation_id)
                    if result is not None:
                        completed[conversation_id] = result
        elif resume:
            # Older interrupted runs may have task summaries but no suite manifest.
            # Those summaries are self-authenticating through their frozen task hash.
            for conversation_id in expected:
                result = load_completed_summary(conversation_id)
                if result is not None:
                    completed[conversation_id] = result

        results: list[ConversationResult] = []
        # Save the run identity before the first model call, then after every boundary.
        write_manifest(results)
        for task in tasks:
            result = completed.get(task.conversation_id) or self.run_task(task, workspace_root / task.conversation_id)
            results.append(result)
            write_manifest(results)
        return results

    def _append_artifacts(self, result: ConversationResult) -> None:
        turn_path = self.output_dir / "conversation_turns.jsonl"
        with turn_path.open("a", encoding="utf-8") as handle:
            for turn in result.turns:
                handle.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")
        summary_path = self.output_dir / f"{result.conversation_id}_summary.json"
        summary_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
