"""Coder-Agent public facade."""

import asyncio
import dataclasses
import sys
import uuid
from threading import Event
from pathlib import Path
from typing import Any

from coder_agent.config import cfg
from coder_agent.core.agent_errors import (
    build_error_guidance,
    build_import_error_guidance,
    build_verification_guidance,
)
from coder_agent.core.agent_loop import run_agent_loop
from coder_agent.core.agent_prompt import SYSTEM_PROMPT, _build_system_prompt
from coder_agent.core.agent_types import (
    TERMINATION_LOOP_EXCEPTION,
    TERMINATION_MAX_STEPS,
    TERMINATION_CANCELLED,
    TERMINATION_MODEL_STOP,
    TERMINATION_RETRY_EXHAUSTED,
    TERMINATION_TOOL_EXCEPTION,
    TERMINATION_TOOL_NONZERO_EXIT,
    TERMINATION_VERIFICATION_FAILED,
    TERMINATION_VERIFICATION_PASSED,
    ModelConfig,
    TurnResult,
    VerificationHook,
    VerificationResult,
)
from coder_agent.core.context import MessageHistory
from coder_agent.core.tool_registry import build_tools
from coder_agent.memory.run_state import RunStateStore, current_git_commit
from coder_agent.memory.trajectory import TrajectoryStore
from coder_agent.tools.base import Tool
from coder_agent.tools.execute import execute_tools


class Agent:
    """ReAct loop: reason -> act -> observe -> repeat."""

    def __init__(
        self,
        tools: list[Tool],
        system: str | None = None,
        model_config: ModelConfig | None = None,
        verbose: bool = cfg.agent.verbose,
        client: Any | None = None,
        memory: Any | None = None,
        trajectory_store: TrajectoryStore | None = None,
        run_state_store: RunStateStore | None = None,
        experiment_id: str = "default",
        experiment_config: dict | None = None,
        runtime_config: dict[str, Any] | None = None,
        workspace: Path | None = None,
        llm_profile_name: str | None = None,
        preset_name: str | None = None,
        owns_run_state_store: bool = False,
    ):
        self._model_cfg = model_config or ModelConfig()
        self.tools = tools
        self.tool_dict = {tool.name: tool for tool in tools}
        self.experiment_id = experiment_id
        self.experiment_config = experiment_config or {}
        self._experiment_config = dict(runtime_config or {})
        self.workspace = Path(workspace or cfg.agent.workspace).resolve()
        self.verbose = verbose
        self.client = client
        self.memory = memory
        self.trajectory_store = trajectory_store
        self.run_state_store = run_state_store
        self._closed = False
        self.llm_profile_name = llm_profile_name
        self.preset_name = preset_name
        self._owns_run_state_store = owns_run_state_store

        if system is not None:
            self.system = system
        else:
            self.system = _build_system_prompt(
                planning_mode=self.experiment_config.get("planning_mode", cfg.agent.planning_mode),
                enable_correction=self.experiment_config.get("correction", cfg.agent.enable_correction),
                workspace=str(self.workspace),
            )

        self.history = MessageHistory(
            model=self._model_cfg.model,
            system=self.system,
            context_window_tokens=self._model_cfg.context_window_tokens,
            client=client,
            experiment_config=self._experiment_config,
        )

        enable_checklist = self.experiment_config.get("checklist", cfg.agent.enable_checklist)
        if enable_checklist:
            from coder_agent.core.decomposer import Decomposer

            self.decomposer: Any = Decomposer()
        else:
            self.decomposer = None

    def _params(self) -> dict[str, Any]:
        return {
            field.name: getattr(self._model_cfg, field.name)
            for field in dataclasses.fields(self._model_cfg)
            if field.name != "context_window_tokens"
        }

    def reset(self) -> None:
        self.system = _build_system_prompt(
            planning_mode=self.experiment_config.get("planning_mode", cfg.agent.planning_mode),
            enable_correction=self.experiment_config.get("correction", cfg.agent.enable_correction),
            workspace=str(self.workspace),
        )
        self.history = MessageHistory(
            model=self._model_cfg.model,
            system=self.system,
            context_window_tokens=self._model_cfg.context_window_tokens,
            client=self.client,
            experiment_config=self._experiment_config,
        )
        if self.decomposer is not None:
            from coder_agent.core.decomposer import Decomposer

            self.decomposer = Decomposer()

    def close(self) -> None:
        if self._closed:
            return
        if self.client is not None and hasattr(self.client, "close"):
            self.client.close()
        if self.memory is not None and hasattr(self.memory, "close"):
            self.memory.close()
        if self._owns_run_state_store and self.run_state_store is not None and hasattr(self.run_state_store, "close"):
            self.run_state_store.close()
        self._closed = True

    def _build_run_config_payload(self) -> dict[str, Any]:
        return {
            "agent_config": dict(self.experiment_config or {}),
            "runtime_config": dict(self._experiment_config or {}),
        }

    def _prepare_run(
        self,
        *,
        user_input: str,
        task_id: str,
        run_id: str | None,
        resume: bool,
    ) -> tuple[str, str | None, dict[str, Any] | None]:
        if self.run_state_store is None:
            if resume:
                raise ValueError("Run-state persistence is disabled; resume is unavailable.")
            return user_input, None, None

        resolved_run_id = run_id or uuid.uuid4().hex
        existing_run = self.run_state_store.get_run(resolved_run_id)
        if resume:
            if existing_run is None:
                raise ValueError(f"Run {resolved_run_id} not found.")
            resolved_user_input = str(existing_run.get("task_description") or "")
            if user_input and resolved_user_input != user_input:
                raise ValueError(
                    f"Run {resolved_run_id} task mismatch. "
                    "Resume must use the original task description."
                )
            if not self.run_state_store.is_resumable_status(existing_run.get("status")):
                raise ValueError(
                    f"Run {resolved_run_id} is already {existing_run['status']} and cannot be resumed."
                )
            checkpoint = self.run_state_store.latest_checkpoint(resolved_run_id)
            resume_state = dict(checkpoint.get("loop_state_json") or {}) if checkpoint else {}
            resume_state["resume_summary"] = self.run_state_store.build_resume_summary(resolved_run_id)
            resume_state["run_started_at"] = existing_run.get("started_at") or resume_state.get("start_time")
            return resolved_user_input, resolved_run_id, resume_state

        if existing_run is not None:
            if existing_run.get("status") == "pending" and existing_run.get("task_description") == user_input:
                return user_input, resolved_run_id, None
            raise ValueError(f"Run {resolved_run_id} already exists.")
        self.run_state_store.create_run(
            resolved_run_id,
            user_input,
            self.experiment_id,
            task_id=task_id or None,
            preset=self.preset_name,
            llm_profile=self.llm_profile_name,
            workspace_path=str(self.workspace),
            git_commit=current_git_commit(),
            config_json=self._build_run_config_payload(),
        )
        return user_input, resolved_run_id, None

    def _make_result(
        self,
        *,
        content: str,
        steps: int,
        tool_calls: list[str],
        success: bool,
        retry_steps: int,
        total_tokens: int,
        trajectory_id: str | None,
        final_status: str,
        termination_reason: str | None,
        error_details: list[str] | None = None,
    ) -> TurnResult:
        return TurnResult(
            content=content,
            steps=steps,
            tool_calls=tool_calls,
            success=success,
            retry_steps=retry_steps,
            total_tokens=total_tokens,
            trajectory_id=trajectory_id,
            final_status=final_status,
            termination_reason=termination_reason,
            error_details=error_details or [],
        )

    def _safe_print(self, text: str = "", end: str = "\n") -> None:
        try:
            print(text, end=end, flush=True)
        except (UnicodeEncodeError, OSError):
            stdout = sys.stdout
            encoding = getattr(stdout, "encoding", None) or "utf-8"
            safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            try:
                print(safe_text, end=end, flush=True)
            except OSError:
                return

    def _build_import_error_guidance(self, stderr_text: str, *, repeated: bool = False) -> str:
        return build_import_error_guidance(stderr_text, repeated=repeated, workspace=self.workspace)

    def _build_error_guidance(
        self,
        error_type: str | None,
        stderr_text: str,
        *,
        repeated: bool = False,
    ) -> str:
        return build_error_guidance(error_type, stderr_text, repeated=repeated, workspace=self.workspace)

    def _build_verification_guidance(
        self,
        summary: str,
        *,
        repeated: bool = False,
        counted_attempt: bool = False,
        preferred_patch_targets: list[str] | None = None,
        stronger_feedback: bool = False,
    ) -> str:
        return build_verification_guidance(
            summary,
            repeated=repeated,
            counted_attempt=counted_attempt,
            preferred_patch_targets=preferred_patch_targets,
            stronger_feedback=stronger_feedback,
        )

    def _record_memory_result(self, user_input: str, result: TurnResult) -> None:
        if self.memory is None:
            return
        project_id = self.memory.get_or_create_project(self.workspace)
        self.memory.record_task(project_id, user_input, result)
        result.extra["db_records_written"] = result.extra.get("db_records_written", 0) + 1

    async def _loop(
        self,
        user_input: str,
        task_id: str = "",
        task_metadata: dict[str, Any] | None = None,
        finalize_trajectory: bool = True,
        record_memory: bool = True,
        verification_hook: VerificationHook | None = None,
        max_verification_attempts: int = 2,
        enforce_stop_verification: bool = True,
        auto_complete_on_verification: bool = False,
        max_steps: int | None = None,
        run_id: str | None = None,
        resume: bool = False,
        resume_state: dict[str, Any] | None = None,
        cancel_event: Event | None = None,
    ) -> TurnResult:
        result = await run_agent_loop(
            self,
            user_input,
            task_id=task_id,
            task_metadata=task_metadata,
            finalize_trajectory=finalize_trajectory,
            verification_hook=verification_hook,
            max_verification_attempts=max_verification_attempts,
            enforce_stop_verification=enforce_stop_verification,
            auto_complete_on_verification=auto_complete_on_verification,
            max_steps=max_steps,
            execute_tools_fn=execute_tools,
            run_id=run_id,
            run_state_store=self.run_state_store,
            resume_state=resume_state if resume else None,
            cancel_event=cancel_event,
        )
        if record_memory and self.memory:
            self._record_memory_result(user_input, result)
        return result

    async def _run_with_cleanup(
        self,
        user_input: str,
        task_id: str = "",
        task_metadata: dict[str, Any] | None = None,
        finalize_trajectory: bool = True,
        record_memory: bool = True,
        verification_hook: VerificationHook | None = None,
        max_verification_attempts: int = 2,
        enforce_stop_verification: bool = True,
        auto_complete_on_verification: bool = False,
        max_steps: int | None = None,
        run_id: str | None = None,
        resume: bool = False,
        resume_state: dict[str, Any] | None = None,
        cancel_event: Event | None = None,
    ) -> TurnResult:
        try:
            return await self._loop(
                user_input,
                task_id=task_id,
                task_metadata=task_metadata,
                finalize_trajectory=finalize_trajectory,
                record_memory=record_memory,
                verification_hook=verification_hook,
                max_verification_attempts=max_verification_attempts,
                enforce_stop_verification=enforce_stop_verification,
                auto_complete_on_verification=auto_complete_on_verification,
                max_steps=max_steps,
                run_id=run_id,
                resume=resume,
                resume_state=resume_state,
                cancel_event=cancel_event,
            )
        finally:
            if self.client is not None and hasattr(self.client, "aclose"):
                await self.client.aclose()

    def run(
        self,
        user_input: str,
        task_id: str = "",
        task_metadata: dict[str, Any] | None = None,
        finalize_trajectory: bool = True,
        record_memory: bool = True,
        verification_hook: VerificationHook | None = None,
        max_verification_attempts: int = 2,
        enforce_stop_verification: bool = True,
        auto_complete_on_verification: bool = False,
        max_steps: int | None = None,
        run_id: str | None = None,
        resume: bool = False,
        cancel_event: Event | None = None,
    ) -> TurnResult:
        prepared_user_input, prepared_run_id, prepared_resume_state = self._prepare_run(
            user_input=user_input,
            task_id=task_id,
            run_id=run_id,
            resume=resume,
        )
        result = asyncio.run(
            self._run_with_cleanup(
                prepared_user_input,
                task_id=task_id,
                task_metadata=task_metadata,
                finalize_trajectory=finalize_trajectory,
                record_memory=False,
                verification_hook=verification_hook,
                max_verification_attempts=max_verification_attempts,
                enforce_stop_verification=enforce_stop_verification,
                auto_complete_on_verification=auto_complete_on_verification,
                max_steps=max_steps,
                run_id=prepared_run_id,
                resume=resume,
                resume_state=prepared_resume_state,
                cancel_event=cancel_event,
            )
        )
        if prepared_run_id is not None:
            result.extra["run_id"] = prepared_run_id
            if resume:
                result.extra["resumed_task"] = prepared_user_input
        if record_memory and self.memory:
            self._record_memory_result(prepared_user_input, result)
        return result


__all__ = [
    "Agent",
    "ModelConfig",
    "SYSTEM_PROMPT",
    "TERMINATION_LOOP_EXCEPTION",
    "TERMINATION_MAX_STEPS",
    "TERMINATION_CANCELLED",
    "TERMINATION_MODEL_STOP",
    "TERMINATION_RETRY_EXHAUSTED",
    "TERMINATION_TOOL_EXCEPTION",
    "TERMINATION_TOOL_NONZERO_EXIT",
    "TERMINATION_VERIFICATION_FAILED",
    "TERMINATION_VERIFICATION_PASSED",
    "TurnResult",
    "VerificationHook",
    "VerificationResult",
    "_build_system_prompt",
    "build_tools",
    "execute_tools",
]
