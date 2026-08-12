from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentRunReport:
    """Versioned payload submitted to POST /v1/ingest/agent/v1.

    Fields mirror the AgentV1IngestRequest schema in llm-evalops-platform.
    """

    schema_version: str = "agent/v1"
    run_id: str = ""
    run_type: str = "service"  # "eval" | "service"
    status: str = ""
    total_steps: int = 0
    total_tool_calls: int = 0
    tool_success_rate: float | None = None
    total_tokens: int = 0
    wall_duration_ms: int = 0
    termination_reason: str | None = None
    git_commit: str | None = None
    preset: str | None = None
    llm_profile: str | None = None
    # eval-only: required for task_set_id computation in the platform
    benchmark_name: str | None = None
    task_ids: list[str] = field(default_factory=list)
