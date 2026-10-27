"""Exact resume, run ownership, orphan recovery, service admission and crash injection."""

import os
import socket
import subprocess
import sys
import threading
import zlib

import pytest

from coder_agent.config import LLMProfile
from coder_agent.core.agent import Agent
from coder_agent.core.agent_types import ModelConfig
from coder_agent.core.checkpoint import (
    decode_history_snapshot,
    encode_history_snapshot,
    history_is_api_consistent,
    workspace_fingerprint,
)
from coder_agent.core.context import MessageHistory
from coder_agent.core.llm_client import LLMClient
from coder_agent.eval.crash_injection import run_case, run_reference
from coder_agent.memory.run_state import RunClaimError, RunStateStore
from coder_agent.tools.file_tools import WriteFileTool


# ---------------------------------------------------------------------------
# Checkpoint payload helpers
# ---------------------------------------------------------------------------

def _history(messages):
    history = MessageHistory(model="m", system="sys", context_window_tokens=1000, client=None)
    history.messages = list(messages)
    history.message_tokens = [(0, 0)] * len(messages)
    return history


def test_history_snapshot_round_trip_and_rejects_bad_payloads():
    messages = [{"role": "user", "content": "hi ✓"}, {"role": "assistant", "content": "ok"}]
    blob = encode_history_snapshot(_history(messages), "system prompt")
    decoded = decode_history_snapshot(blob)
    assert decoded["messages"] == messages
    assert decoded["schema"] == "history/v1"

    assert decode_history_snapshot(None) is None
    assert decode_history_snapshot(b"not zlib") is None
    assert decode_history_snapshot(zlib.compress(b'{"schema": "history/v0"}')) is None


def test_history_api_consistency_requires_answered_tool_calls():
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "x"}}]}
    answered = [{"role": "user", "content": "go"}, call, {"role": "tool", "tool_call_id": "c1", "content": "r"}]
    assert history_is_api_consistent(answered)
    assert not history_is_api_consistent(answered[:2])
    assert not history_is_api_consistent(answered[:2] + [{"role": "user", "content": "next"}])


def test_workspace_fingerprint_tracks_edits_ignores_caches_and_caps_size(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    before = workspace_fingerprint(tmp_path)

    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-312.pyc").write_bytes(b"\0")
    assert workspace_fingerprint(tmp_path) == before

    (tmp_path / "a.py").write_text("x = 22\n")
    assert workspace_fingerprint(tmp_path) != before
    assert workspace_fingerprint(tmp_path, max_files=0) is None
    assert workspace_fingerprint(tmp_path / "missing") is None


# ---------------------------------------------------------------------------
# In-process exact resume
# ---------------------------------------------------------------------------

class _RecordingBackend:
    """Writes stepN.txt for steps 1..4 based on the history, then finishes."""

    def __init__(self):
        self.requests = []

    async def chat(self, *, messages, **_kwargs):
        self.requests.append([dict(message) for message in messages])
        step = 1 + sum(1 for message in messages if message.get("role") == "assistant")
        usage = {"input_tokens": 10 * step, "output_tokens": 1}
        if step > 4:
            return {"content": [{"type": "text", "text": "done"}], "tool_uses": [], "parse_errors": [], "usage": usage}
        call = {"id": f"call_{step}", "name": "write_file",
                "input": {"operation": "write", "path": f"step{step}.txt", "content": f"{step}\n"}}
        return {"content": [{"type": "text", "text": f"step {step}"}], "tool_uses": [call], "parse_errors": [],
                "usage": usage}

    async def aclose(self):
        return None


def _agent(workspace, store):
    backend = _RecordingBackend()
    client = LLMClient(profile=LLMProfile(name="t", transport="openai", model="m", api_key="k", base_url=None))
    client._backend = backend
    agent = Agent(tools=[WriteFileTool(workspace)], client=client, model_config=ModelConfig(),
                  run_state_store=store, workspace=workspace, verbose=False)
    return agent, backend


def _interrupted_run(tmp_path, name="interrupted"):
    """Run two steps, stop at the step budget (a resumable 'timeout'), return paths."""
    workspace = tmp_path / name
    workspace.mkdir()
    store = RunStateStore(tmp_path / f"{name}.db")
    agent, _ = _agent(workspace, store)
    first = agent.run("write files", run_id="r", record_memory=False, max_steps=2)
    assert first.final_status != "success"
    return workspace, store


def test_exact_resume_sends_the_same_model_requests_as_an_uninterrupted_run(tmp_path):
    reference_ws = tmp_path / "reference"
    reference_ws.mkdir()
    reference_agent, reference_backend = _agent(reference_ws, RunStateStore(tmp_path / "reference.db"))
    reference = reference_agent.run("write files", run_id="r", record_memory=False, max_steps=8)

    workspace, store = _interrupted_run(tmp_path)
    resumed_agent, resumed_backend = _agent(workspace, store)  # fresh process state
    result = resumed_agent.run("", run_id="r", resume=True, record_memory=False, max_steps=8)

    assert result.success is True
    assert result.extra["resume_mode"] == "exact"
    # Steps 3..5 after resume see exactly the history the uninterrupted run saw.
    assert resumed_backend.requests == reference_backend.requests[2:]
    assert [step["step_index"] for step in store.list_steps("r")] == [0, 1, 2, 3, 4]
    # Usage recorded before the interruption is carried into the resumed run.
    assert result.usage["llm_calls"] == reference.usage["llm_calls"] == 5
    assert result.total_tokens == reference.total_tokens


def test_legacy_summary_resume_is_detectably_inexact(tmp_path):
    """Negative control: without a snapshot the resumed request differs."""
    reference_ws = tmp_path / "reference"
    reference_ws.mkdir()
    reference_agent, reference_backend = _agent(reference_ws, RunStateStore(tmp_path / "reference.db"))
    reference_agent.run("write files", run_id="r", record_memory=False, max_steps=8)

    workspace, store = _interrupted_run(tmp_path)
    with store._lock:
        store._conn.execute("UPDATE run_steps SET history_snapshot = NULL")
        store._conn.commit()
    resumed_agent, resumed_backend = _agent(workspace, store)
    result = resumed_agent.run("", run_id="r", resume=True, record_memory=False, max_steps=8)

    assert result.extra["resume_mode"] == "summary"
    assert resumed_backend.requests[0] != reference_backend.requests[2]
    assert any("[Resume context]" in str(m.get("content")) for m in resumed_backend.requests[0])


def test_resume_after_workspace_change_restores_history_and_adds_notice(tmp_path):
    workspace, store = _interrupted_run(tmp_path)
    (workspace / "step3.txt").write_text("partial write from the interrupted step\n")

    resumed_agent, resumed_backend = _agent(workspace, store)
    result = resumed_agent.run("", run_id="r", resume=True, record_memory=False, max_steps=8)

    assert result.extra["resume_mode"] == "exact_workspace_drift"
    notices = [m for m in resumed_backend.requests[0] if str(m.get("content")).startswith("[Resume notice]")]
    assert len(notices) == 1
    assert result.success is True


def test_resume_uses_last_snapshot_boundary_and_discards_later_rows(tmp_path):
    workspace, store = _interrupted_run(tmp_path)
    # An exception-path row after the last boundary carries no snapshot.
    store.record_step("r", 2, thought="[system] crash", observation="boom", tool_call_count=0,
                      had_error=True, step_tokens=0, step_duration_ms=0, loop_state={"steps": 3})
    assert store.latest_resumable_checkpoint("r")["step_index"] == 1

    resumed_agent, _ = _agent(workspace, store)
    result = resumed_agent.run("", run_id="r", resume=True, record_memory=False, max_steps=8)

    assert result.extra["resume_mode"] == "exact"
    steps = store.list_steps("r")
    assert [step["step_index"] for step in steps] == [0, 1, 2, 3, 4]
    assert "[system] crash" not in [step["thought_text"] for step in steps]


# ---------------------------------------------------------------------------
# Ownership and orphan recovery
# ---------------------------------------------------------------------------

def _set_owner(store, run_id, *, host, pid, heartbeat=None):
    with store._lock:
        store._conn.execute(
            "UPDATE runs SET status='running', owner_host=?, owner_pid=?, heartbeat_at=? WHERE run_id=?",
            (host, pid, heartbeat, run_id),
        )
        store._conn.commit()


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_run_owned_by_live_process_cannot_be_claimed_or_resumed(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("r", "write files", "cli")
    _set_owner(store, "r", host=socket.gethostname(), pid=os.getppid())

    with pytest.raises(RunClaimError):
        store.start_run("r")
    agent, _ = _agent(tmp_path, store)
    with pytest.raises(ValueError, match="currently running"):
        agent.run("", run_id="r", resume=True, record_memory=False)
    assert store.reconcile_orphans() == []


def test_dead_owner_run_is_reconciled_to_interrupted_and_claimable(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("r", "write files", "cli")
    _set_owner(store, "r", host=socket.gethostname(), pid=_dead_pid())

    assert store.claim_blocker(store.get_run("r")) is None
    assert store.reconcile_orphans() == ["r"]
    assert store.get_run("r")["status"] == "interrupted"
    store.start_run("r")
    run = store.get_run("r")
    assert (run["status"], run["owner_pid"]) == ("running", os.getpid())


def test_remote_owner_is_trusted_until_its_heartbeat_goes_stale(tmp_path):
    import time

    store = RunStateStore(tmp_path / "run_state.db", orphan_stale_after_seconds=60)
    store.create_run("r", "write files", "cli")
    _set_owner(store, "r", host="other-host", pid=1, heartbeat=time.time())
    assert store.claim_blocker(store.get_run("r")) is not None
    assert store.reconcile_orphans() == []

    _set_owner(store, "r", host="other-host", pid=1, heartbeat=time.time() - 120)
    assert store.reconcile_orphans() == ["r"]


def test_run_state_store_uses_wal(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# ---------------------------------------------------------------------------
# Service admission
# ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from coder_agent.service.app import RuntimeService, create_app  # noqa: E402


@pytest.fixture
def blocked_service(tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(RuntimeService, "_run_in_background", lambda self, *args: release.wait(5))
    store = RunStateStore(tmp_path / "run_state.db")
    app = create_app(run_state_store=store)
    app.state.runtime_service.max_concurrent_runs = 1
    yield TestClient(app), store, release
    release.set()


def test_service_rejects_submits_beyond_capacity_with_429(blocked_service):
    client, _, release = blocked_service
    assert client.post("/runs", json={"task": "one"}).status_code == 200
    busy = client.post("/runs", json={"task": "two"})
    assert busy.status_code == 429
    assert busy.headers["Retry-After"] == "30"


def test_service_idempotency_key_replays_or_conflicts(blocked_service):
    client, store, release = blocked_service
    headers = {"Idempotency-Key": "submit-1"}
    first = client.post("/runs", json={"task": "one"}, headers=headers).json()
    # A replay does not take a run slot, even though the only slot is busy.
    replay = client.post("/runs", json={"task": "one"}, headers=headers).json()
    conflict = client.post("/runs", json={"task": "different"}, headers=headers)

    assert first["replayed"] is False
    assert replay == {**first, "replayed": True}
    assert conflict.status_code == 409
    assert len(store.list_runs()) == 1


def test_service_startup_marks_runs_of_dead_processes_interrupted(tmp_path):
    store = RunStateStore(tmp_path / "run_state.db")
    store.create_run("orphan", "write files", "service")
    _set_owner(store, "orphan", host=socket.gethostname(), pid=_dead_pid())

    app = create_app(run_state_store=store)

    assert app.state.runtime_service.recovered_run_ids == ["orphan"]
    assert TestClient(app).get("/runs/orphan").json()["run"]["status"] == "interrupted"


# ---------------------------------------------------------------------------
# Out-of-process crash injection (abrupt exit and real SIGKILL)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def crash_reference(tmp_path_factory):
    return run_reference(tmp_path_factory.mktemp("crash-reference"), steps=3)


@pytest.mark.parametrize(
    ("crash_at", "expected_mode"),
    [
        ("llm:1", "restart"),
        ("tool:1", "restart_workspace_drift"),
        ("llm:2", "exact"),
        ("tool:2", "exact_workspace_drift"),
        ("checkpoint:2", "exact"),
    ],
)
def test_crash_injection_resumes_to_the_uninterrupted_result(tmp_path, crash_reference, crash_at, expected_mode):
    case = run_case(tmp_path, steps=3, reference=crash_reference, crash_at=crash_at)
    assert case["crashed"] is True
    assert case["resume_mode"] == expected_mode
    assert case["checks"] == {key: True for key in case["checks"]}, case["stderr_tail"]


def test_real_sigkill_mid_run_resumes_to_the_uninterrupted_result(tmp_path, crash_reference):
    case = run_case(tmp_path, steps=3, reference=crash_reference, sigkill_after=0.08, delay=0.05)
    assert case["passed"] is True, (case["checks"], case["stderr_tail"])
