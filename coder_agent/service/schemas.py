from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    task: str = Field(..., min_length=1)
    model: str | None = None
    llm_profile: str | None = None
    preset: str | None = None
    workspace: str | None = None
    no_memory: bool = False
    max_steps: int | None = Field(default=None, ge=1)


class RunCreateResponse(BaseModel):
    run_id: str
    status: str


class RunCancelResponse(BaseModel):
    run_id: str
    status: str


class RunRecordModel(BaseModel):
    run_id: str
    task_id: str | None = None
    experiment_id: str
    preset: str | None = None
    llm_profile: str | None = None
    workspace_path: str | None = None
    task_description: str
    status: str
    started_at: float | None = None
    finished_at: float | None = None
    total_steps: int
    total_tool_calls: int
    total_tokens: int
    tool_success_rate: float | None = None
    termination_reason: str | None = None
    error_summary: str | None = None
    git_commit: str | None = None
    config_json: dict[str, Any] | str | None = None
    created_at: float
    wall_duration_ms: int = 0


class RunListResponse(BaseModel):
    runs: list[RunRecordModel]


class RunDetailResponse(BaseModel):
    run: RunRecordModel


class RunStepModel(BaseModel):
    step_index: int
    thought_text: str | None = None
    observation_text: str | None = None
    tool_call_count: int
    had_error: int
    step_tokens: int
    step_duration_ms: int
    loop_state_json: dict[str, Any] | str | None = None
    recorded_at: float


class RunStepsResponse(BaseModel):
    run_id: str
    steps: list[RunStepModel]


class HealthResponse(BaseModel):
    status: str
