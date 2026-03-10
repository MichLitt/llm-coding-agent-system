"""Coder-Agent public facade."""

import asyncio
import dataclasses
import sys
from typing import Any

from coder_agent.config import cfg
from coder_agent.core.agent_errors import build_error_guidance, build_import_error_guidance
from coder_agent.core.agent_loop import run_agent_loop
from coder_agent.core.agent_prompt import SYSTEM_PROMPT, _build_system_prompt
from coder_agent.core.agent_types import (
    TERMINATION_LOOP_EXCEPTION,
    TERMINATION_MAX_STEPS,
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
        experiment_id: str = "default",
        experiment_config: dict | None = None,
    ):
        self._model_cfg = model_config or ModelConfig()
        self.tools = tools
        self.tool_dict = {tool.name: tool for tool in tools}
        self.experiment_id = experiment_id
        self.experiment_config = experiment_config or {}
        self.verbose = verbose
        self.client = client
        self.memory = memory
        self.trajectory_store = trajectory_store

        if system is not None:
            self.system = system
        else:
            self.system = _build_system_prompt(
                planning_mode=self.experiment_config.get("planning_mode", cfg.agent.planning_mode),
                enable_correction=self.experiment_config.get("correction", cfg.agent.enable_correction),
            )

        self.history = MessageHistory(
            model=self._model_cfg.model,
            system=self.system,
            context_window_tokens=self._model_cfg.context_window_tokens,
            client=client,
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
        )
        self.history = MessageHistory(
            model=self._model_cfg.model,
            system=self.system,
            context_window_tokens=self._model_cfg.context_window_tokens,
            client=self.client,
        )
        if self.decomposer is not None:
            from coder_agent.core.decomposer import Decomposer

            self.decomposer = Decomposer()

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
        return build_import_error_guidance(stderr_text, repeated=repeated)

    def _build_error_guidance(
        self,
        error_type: str | None,
        stderr_text: str,
        *,
        repeated: bool = False,
    ) -> str:
        return build_error_guidance(error_type, stderr_text, repeated=repeated)

    async def _loop(
        self,
        user_input: str,
        task_id: str = "",
        finalize_trajectory: bool = True,
        verification_hook: VerificationHook | None = None,
        max_verification_attempts: int = 2,
        enforce_stop_verification: bool = True,
        auto_complete_on_verification: bool = False,
    ) -> TurnResult:
        return await run_agent_loop(
            self,
            user_input,
            task_id=task_id,
            finalize_trajectory=finalize_trajectory,
            verification_hook=verification_hook,
            max_verification_attempts=max_verification_attempts,
            enforce_stop_verification=enforce_stop_verification,
            auto_complete_on_verification=auto_complete_on_verification,
            execute_tools_fn=execute_tools,
        )

    def run(
        self,
        user_input: str,
        task_id: str = "",
        finalize_trajectory: bool = True,
        verification_hook: VerificationHook | None = None,
        max_verification_attempts: int = 2,
        enforce_stop_verification: bool = True,
        auto_complete_on_verification: bool = False,
    ) -> TurnResult:
        return asyncio.run(
            self._loop(
                user_input,
                task_id=task_id,
                finalize_trajectory=finalize_trajectory,
                verification_hook=verification_hook,
                max_verification_attempts=max_verification_attempts,
                enforce_stop_verification=enforce_stop_verification,
                auto_complete_on_verification=auto_complete_on_verification,
            )
        )


__all__ = [
    "Agent",
    "ModelConfig",
    "SYSTEM_PROMPT",
    "TERMINATION_LOOP_EXCEPTION",
    "TERMINATION_MAX_STEPS",
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
