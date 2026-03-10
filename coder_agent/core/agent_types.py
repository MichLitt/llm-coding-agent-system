import dataclasses
from dataclasses import dataclass
from typing import Awaitable, Callable

from coder_agent.config import cfg


TERMINATION_MODEL_STOP = "model_stop"
TERMINATION_TOOL_NONZERO_EXIT = "tool_nonzero_exit"
TERMINATION_TOOL_EXCEPTION = "tool_exception"
TERMINATION_RETRY_EXHAUSTED = "retry_exhausted"
TERMINATION_LOOP_EXCEPTION = "loop_exception"
TERMINATION_MAX_STEPS = "max_steps"
TERMINATION_VERIFICATION_FAILED = "verification_failed"
TERMINATION_VERIFICATION_PASSED = "verification_passed"


@dataclass
class ModelConfig:
    model: str = cfg.model.name
    max_tokens: int = cfg.model.max_tokens
    temperature: float = cfg.model.temperature
    context_window_tokens: int = cfg.context.context_window_tokens


@dataclass
class TurnResult:
    content: str
    steps: int
    tool_calls: list[str]
    success: bool
    retry_steps: int = 0
    total_tokens: int = 0
    trajectory_id: str | None = None
    final_status: str = "failed"
    termination_reason: str | None = None
    error_details: list[str] = dataclasses.field(default_factory=list)


@dataclass
class VerificationResult:
    passed: bool
    summary: str = ""


VerificationHook = Callable[[], VerificationResult | Awaitable[VerificationResult]]
