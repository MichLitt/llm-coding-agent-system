import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from coder_agent.memory.run_state import RunStateStore
from coder_agent.service.app import RuntimeService, create_app
from coder_agent.service.schemas import RunCreateRequest


def test_service_api_exposes_run_and_steps(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("run-1", "demo task", "service")
    store.start_run("run-1")
    store.record_step(
        "run-1",
        0,
        thought="inspect file",
        observation="read ok",
        tool_call_count=1,
        had_error=False,
        step_tokens=12,
        step_duration_ms=40,
        loop_state={"steps": 1},
    )

    app = create_app(run_state_store=store)
    client = TestClient(app)

    health_response = client.get("/health")
    list_response = client.get("/runs")
    run_response = client.get("/runs/run-1")
    steps_response = client.get("/runs/run-1/steps")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert list_response.status_code == 200
    assert list_response.json()["runs"][0]["run_id"] == "run-1"
    assert run_response.status_code == 200
    assert run_response.json()["run"]["status"] == "running"
    assert steps_response.status_code == 200
    assert steps_response.json()["steps"][0]["thought_text"] == "inspect file"


def test_service_api_submit_returns_run_id(tmp_path, monkeypatch):
    store = RunStateStore(tmp_path / "run_state.db")
    app = create_app(run_state_store=store)
    client = TestClient(app)

    monkeypatch.setattr(app.state.runtime_service, "submit_run", lambda request: "run-post-1")

    response = client.post("/runs", json={"task": "demo task"})

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-post-1", "status": "pending"}


def test_service_api_cancel_endpoint(tmp_path, monkeypatch):
    store = RunStateStore(tmp_path / "run_state.db")
    app = create_app(run_state_store=store)
    client = TestClient(app)

    monkeypatch.setattr(app.state.runtime_service, "cancel_run", lambda run_id: "cancelling")

    response = client.post("/runs/run-1/cancel")

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "status": "cancelling"}


def test_runtime_service_forwards_max_steps(tmp_path, monkeypatch):
    store = RunStateStore(tmp_path / "run_state.db")
    service = RuntimeService(run_state_store=store)
    captured = {}

    class FakeAgent:
        def run(self, task, **kwargs):
            captured["task"] = task
            captured["kwargs"] = kwargs

        def close(self):
            return None

    monkeypatch.setattr("coder_agent.service.app.make_agent", lambda **kwargs: FakeAgent())

    request = RunCreateRequest(task="demo task", max_steps=7)
    service._run_in_background("run-1", request, "default")

    assert captured["task"] == "demo task"
    assert captured["kwargs"]["max_steps"] == 7
