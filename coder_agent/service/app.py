from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from coder_agent.cli.factory import make_agent, make_run_state_store, resolve_agent_config
from coder_agent.config import cfg
from coder_agent.memory.run_state import RunMetrics, RunStateStore, current_git_commit
from coder_agent.service.schemas import (
    HealthResponse,
    RunCancelResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunDetailResponse,
    RunListResponse,
    RunStepsResponse,
)


class RuntimeService:
    def __init__(self, run_state_store: RunStateStore | None = None):
        self.run_state_store = run_state_store or make_run_state_store()
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit_run(self, request: RunCreateRequest) -> str:
        run_id = uuid.uuid4().hex
        workspace = str(Path(request.workspace).resolve()) if request.workspace else None
        preset_name = request.preset or "default"
        self.run_state_store.create_run(
            run_id,
            request.task,
            experiment_id="service",
            preset=request.preset,
            llm_profile=request.llm_profile,
            workspace_path=workspace,
            git_commit=current_git_commit(),
            config_json={
                "source": "service",
                "preset": request.preset,
                "workspace": workspace,
                "max_steps": request.max_steps,
            },
        )
        thread = threading.Thread(
            target=self._run_in_background,
            args=(run_id, request, preset_name),
            daemon=True,
            name=f"coder-agent-run-{run_id[:8]}",
        )
        with self._lock:
            self._cancel_events[run_id] = threading.Event()
            self._threads[run_id] = thread
        thread.start()
        return run_id

    def _run_in_background(self, run_id: str, request: RunCreateRequest, preset_name: str) -> None:
        agent = None
        try:
            agent_cfg = resolve_agent_config(preset_name)
            workspace = Path(request.workspace).resolve() if request.workspace else None
            agent = make_agent(
                agent_config=agent_cfg,
                workspace=workspace,
                model=request.model,
                llm_profile=request.llm_profile,
                no_memory=request.no_memory,
                experiment_id="service",
                run_state_store=self.run_state_store,
                preset_name=request.preset,
            )
            with self._lock:
                cancel_event = self._cancel_events.get(run_id)
            agent.run(request.task, run_id=run_id, max_steps=request.max_steps, cancel_event=cancel_event)
        except Exception as exc:
            run_row = self.run_state_store.get_run(run_id)
            if run_row is not None and run_row.get("status") in {"pending", "running"}:
                self.run_state_store.finish_run(
                    run_id,
                    "failed",
                    "service_background_exception",
                    str(exc),
                    RunMetrics(
                        total_steps=int(run_row.get("total_steps", 0) or 0),
                        total_tool_calls=int(run_row.get("total_tool_calls", 0) or 0),
                        total_tokens=int(run_row.get("total_tokens", 0) or 0),
                        tool_success_rate=run_row.get("tool_success_rate"),
                    ),
                )
        finally:
            if agent is not None and hasattr(agent, "close"):
                agent.close()
            with self._lock:
                self._threads.pop(run_id, None)
                self._cancel_events.pop(run_id, None)

    def _normalize_run(self, run: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(run)
        if normalized.get("started_at") and normalized.get("finished_at"):
            normalized["wall_duration_ms"] = int((normalized["finished_at"] - normalized["started_at"]) * 1000)
        elif normalized.get("started_at"):
            normalized["wall_duration_ms"] = int((time.time() - normalized["started_at"]) * 1000)
        else:
            normalized["wall_duration_ms"] = 0
        return normalized

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.run_state_store.get_run(run_id)
        if run is None:
            return None
        return self._normalize_run(run)

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [self._normalize_run(run) for run in self.run_state_store.list_runs(limit=limit)]

    def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        return self.run_state_store.list_steps(run_id)

    def cancel_run(self, run_id: str) -> str | None:
        run = self.run_state_store.get_run(run_id)
        if run is None:
            return None
        if run.get("status") in {"success", "failed", "cancelled", "timeout"}:
            return str(run["status"])
        with self._lock:
            cancel_event = self._cancel_events.get(run_id)
            thread = self._threads.get(run_id)
        if cancel_event is not None and thread is not None and thread.is_alive():
            cancel_event.set()
            return "cancelling"
        if self.run_state_store.cancel_run(run_id):
            return "cancelled"
        refreshed = self.run_state_store.get_run(run_id)
        return None if refreshed is None else str(refreshed.get("status", "unknown"))


def create_app(run_state_store: RunStateStore | None = None) -> FastAPI:
    app = FastAPI(title="Coder Agent Runtime API", version="0.7.2")
    service = RuntimeService(run_state_store=run_state_store)
    app.state.runtime_service = service

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post("/runs", response_model=RunCreateResponse)
    def create_run(request: RunCreateRequest) -> RunCreateResponse:
        run_id = service.submit_run(request)
        return RunCreateResponse(run_id=run_id, status="pending")

    @app.get("/runs", response_model=RunListResponse)
    def list_runs(limit: int = 50) -> RunListResponse:
        return RunListResponse(runs=service.list_runs(limit=limit))

    @app.get("/runs/{run_id}", response_model=RunDetailResponse)
    def get_run(run_id: str) -> RunDetailResponse:
        run = service.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        return RunDetailResponse(run=run)

    @app.post("/runs/{run_id}/cancel", response_model=RunCancelResponse)
    def cancel_run(run_id: str) -> RunCancelResponse:
        status = service.cancel_run(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        return RunCancelResponse(run_id=run_id, status=status)

    @app.get("/runs/{run_id}/steps", response_model=RunStepsResponse)
    def get_steps(run_id: str) -> RunStepsResponse:
        run = service.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        return RunStepsResponse(run_id=run_id, steps=service.get_steps(run_id))

    return app


def run_server(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=host or cfg.service.host, port=port or cfg.service.port)


__all__ = ["RuntimeService", "create_app", "run_server"]
