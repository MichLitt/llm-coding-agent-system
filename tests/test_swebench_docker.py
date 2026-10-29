"""SWE-bench Docker runner: predictions, records, resume and failure isolation (fake Docker)."""

import json
import subprocess
from pathlib import Path

from coder_agent.config import LLMProfile
from coder_agent.core.agent import Agent
from coder_agent.core.agent_types import ModelConfig
from coder_agent.core.llm_client import LLMClient
from coder_agent.eval.swebench_docker import PROMPT_TEMPLATE, is_provider_failure, load_instances, run_instances
from coder_agent.tools.container import DockerSandbox, build_container_tools
from tests.test_container_tools import FakeDocker


def _git_repo(root):
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True)


class _FixBackend:
    def __init__(self):
        self.prompts = []

    async def chat(self, *, messages, **_kwargs):
        self.prompts.append(messages[0]["content"])
        if not any(message.get("role") == "assistant" for message in messages):
            call = {"id": "c1", "name": "patch_file",
                    "input": {"path": "calc.py", "edits": [{"old_text": "a - b", "new_text": "a + b"}]}}
            return {"content": [], "tool_uses": [call], "parse_errors": [], "usage": {"input_tokens": 50, "output_tokens": 5}}
        return {"content": [{"type": "text", "text": "Fixed add."}], "tool_uses": [], "parse_errors": [],
                "usage": {"input_tokens": 60, "output_tokens": 3}}

    async def aclose(self):
        return None


def _factories(tmp_path, backend):
    def start_sandbox(instance_id, options):
        root = tmp_path / "containers" / instance_id
        _git_repo(root)
        return DockerSandbox(instance_id, runner=FakeDocker(root), timeout_wrapper=False)

    def agent_factory(sandbox, options):
        client = LLMClient(profile=LLMProfile(name="t", transport="openai", model="m", api_key="k", base_url=None))
        client._backend = backend
        return Agent(tools=build_container_tools(sandbox), client=client, model_config=ModelConfig(),
                     workspace=Path(sandbox.workdir), verbose=False)

    return start_sandbox, agent_factory


def _options(tmp_path):
    return {"output": tmp_path / "out", "label": "test-run", "max_steps": 5}


def test_runner_writes_official_predictions_and_records(tmp_path):
    backend = _FixBackend()
    start_sandbox, agent_factory = _factories(tmp_path, backend)
    instances = [{"instance_id": "demo__calc-1", "problem_statement": "add() subtracts instead of adding"}]

    summary = run_instances(instances, _options(tmp_path), start_sandbox=start_sandbox, agent_factory=agent_factory)

    [prediction] = [json.loads(line) for line in (tmp_path / "out/predictions.jsonl").read_text().splitlines()]
    [record] = [json.loads(line) for line in (tmp_path / "out/results.jsonl").read_text().splitlines()]
    assert summary["ran"] == 1
    assert set(prediction) == {"instance_id", "model_name_or_path", "model_patch"}
    assert prediction["model_name_or_path"] == "test-run"
    assert "-    return a - b" in prediction["model_patch"] and "+    return a + b" in prediction["model_patch"]
    assert record["error"] is None and record["final_status"] == "success"
    assert record["usage"]["total_tokens"] == 118
    assert backend.prompts[0] == PROMPT_TEMPLATE.format(problem_statement="add() subtracts instead of adding")


def test_runner_resumes_and_isolates_instance_failures(tmp_path):
    backend = _FixBackend()
    start_sandbox, agent_factory = _factories(tmp_path, backend)

    def flaky_start(instance_id, options):
        if instance_id == "demo__broken-2":
            raise RuntimeError("image not found")
        return start_sandbox(instance_id, options)

    instances = [
        {"instance_id": "demo__calc-1", "problem_statement": "p"},
        {"instance_id": "demo__broken-2", "problem_statement": "p"},
    ]
    run_instances(instances[:1], _options(tmp_path), start_sandbox=flaky_start, agent_factory=agent_factory)
    summary = run_instances(instances, _options(tmp_path), start_sandbox=flaky_start, agent_factory=agent_factory)

    records = {r["instance_id"]: r for r in map(json.loads, (tmp_path / "out/results.jsonl").read_text().splitlines())}
    predictions = [json.loads(line) for line in (tmp_path / "out/predictions.jsonl").read_text().splitlines()]
    assert summary == {
        "skipped_completed": 1, "ran": 1, "retried": 0, "predictions": str(tmp_path / "out/predictions.jsonl"),
    }
    assert [p["instance_id"] for p in predictions] == ["demo__calc-1", "demo__broken-2"]
    assert predictions[1]["model_patch"] == ""
    assert records["demo__broken-2"]["error"] == "RuntimeError: image not found"


def test_load_instances_requires_public_fields(tmp_path):
    path = tmp_path / "instances.jsonl"
    path.write_text(json.dumps({"instance_id": "x"}) + "\n")
    try:
        load_instances(path)
    except ValueError as exc:
        assert "problem_statement" in str(exc)
    else:
        raise AssertionError("missing problem_statement must be rejected")


def test_manifest_records_model_settings_and_refuses_resume_when_they_change(tmp_path, monkeypatch):
    from coder_agent.config import cfg, resolve_llm_profile
    from coder_agent.eval import swebench_docker

    instances = tmp_path / "instances.jsonl"
    instances.write_text(json.dumps({"instance_id": "demo__calc-1", "problem_statement": "p"}) + "\n")
    monkeypatch.setattr(swebench_docker, "run_instances", lambda *args, **kwargs: {"ran": 0})
    argv = ["--instances", str(instances), "--output", str(tmp_path / "out"), "--label", "l", "--llm-profile", "glm_5"]

    swebench_docker.main(argv)
    manifest = json.loads((tmp_path / "out/manifest.json").read_text())
    assert manifest["model_settings"] == {
        "temperature": cfg.model.temperature, "max_tokens": cfg.model.max_tokens, "extra_body": None,
        "base_url": resolve_llm_profile("glm_5").base_url,
    }

    monkeypatch.setattr(cfg.model, "max_tokens", cfg.model.max_tokens + 1)
    try:
        swebench_docker.main(argv)
    except SystemExit as exc:
        assert "model_settings" in str(exc)
    else:
        raise AssertionError("a changed token budget must not resume into the same run")


def test_manifest_records_the_profile_request_fields(tmp_path, monkeypatch):
    from coder_agent.eval import swebench_docker

    instances = tmp_path / "instances.jsonl"
    instances.write_text(json.dumps({"instance_id": "demo__calc-1", "problem_statement": "p"}) + "\n")
    monkeypatch.setattr(swebench_docker, "run_instances", lambda *args, **kwargs: {"ran": 0})

    swebench_docker.main(["--instances", str(instances), "--output", str(tmp_path / "out"), "--label", "l",
                          "--llm-profile", "glm_5_nothink"])

    manifest = json.loads((tmp_path / "out/manifest.json").read_text())
    assert manifest["model_settings"]["extra_body"] == {"thinking": {"type": "disabled"}}


class _EndlessBackend:
    """Patches once, then keeps listing the directory: never finishes on its own."""

    def __init__(self, delay):
        self.delay = delay

    async def chat(self, *, messages, **_kwargs):
        import asyncio

        await asyncio.sleep(self.delay)
        if not any(message.get("role") == "assistant" for message in messages):
            call = {"id": "c0", "name": "patch_file",
                    "input": {"path": "calc.py", "edits": [{"old_text": "a - b", "new_text": "a + b"}]}}
        else:
            call = {"id": f"c{len(messages)}", "name": "list_dir", "input": {"path": "."}}
        return {"content": [], "tool_uses": [call], "parse_errors": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

    async def aclose(self):
        return None


def test_wall_cap_stops_the_run_and_discards_its_patch(tmp_path):
    start_sandbox, agent_factory = _factories(tmp_path, _EndlessBackend(delay=0.05))
    options = {**_options(tmp_path), "max_steps": 500, "wall_cap_seconds": 0.4}

    run_instances([{"instance_id": "demo__calc-1", "problem_statement": "p"}], options,
                  start_sandbox=start_sandbox, agent_factory=agent_factory)

    [record] = [json.loads(line) for line in (tmp_path / "out/results.jsonl").read_text().splitlines()]
    [prediction] = [json.loads(line) for line in (tmp_path / "out/predictions.jsonl").read_text().splitlines()]
    assert record["budget_failure"] == "wall_cap"
    assert record["termination_reason"] == "cancelled"
    assert prediction["model_patch"] == ""
    assert "+    return a + b" in (tmp_path / "out/discarded_patches/demo__calc-1.diff").read_text()


def test_retry_errors_reruns_infrastructure_failures_once_and_grades_each_instance_once(tmp_path):
    start_sandbox, agent_factory = _factories(tmp_path, _FixBackend())
    starts = {"demo__calc-1": 0, "demo__down-2": 0}

    def flaky_start(instance_id, options):
        starts[instance_id] += 1
        if instance_id == "demo__down-2" or starts[instance_id] == 1:
            raise RuntimeError("image pull failed")
        return start_sandbox(instance_id, options)

    instances = [{"instance_id": "demo__calc-1", "problem_statement": "p"},
                 {"instance_id": "demo__down-2", "problem_statement": "p"}]
    options = {**_options(tmp_path), "retry_errors": True}
    first = run_instances(instances, options, start_sandbox=flaky_start, agent_factory=agent_factory)
    # Later invocations find nothing left to retry: two attempts is the cap.
    for _ in range(2):
        run_instances(instances, options, start_sandbox=flaky_start, agent_factory=agent_factory)

    records = [json.loads(line) for line in (tmp_path / "out/results.jsonl").read_text().splitlines()]
    predictions = [json.loads(line) for line in (tmp_path / "out/predictions.jsonl").read_text().splitlines()]
    assert starts == {"demo__calc-1": 2, "demo__down-2": 2}
    assert [(r["instance_id"], r["attempt"], r["error"] is None) for r in records] == [
        ("demo__calc-1", 1, False), ("demo__down-2", 1, False),
        ("demo__calc-1", 2, True), ("demo__down-2", 2, False),
    ]
    assert [p["instance_id"] for p in predictions] == ["demo__calc-1", "demo__down-2"]
    assert "+    return a + b" in predictions[0]["model_patch"]
    assert (first["ran"], first["retried"]) == (4, 2)



def test_a_higher_attempt_cap_lets_a_later_invocation_retry_exhausted_failures(tmp_path):
    start_sandbox, agent_factory = _factories(tmp_path, _FixBackend())
    starts = {"demo__calc-1": 0}

    def flaky_start(instance_id, options):
        starts[instance_id] += 1
        if starts[instance_id] <= 2:
            raise RuntimeError("image pull failed")
        return start_sandbox(instance_id, options)

    instances = [{"instance_id": "demo__calc-1", "problem_statement": "p"}]
    options = {**_options(tmp_path), "retry_errors": True}
    run_instances(instances, options, start_sandbox=flaky_start, agent_factory=agent_factory)
    # Both attempts the default cap allows failed; a higher cap retries the
    # instance, and once it succeeds nothing is left to retry.
    for _ in range(2):
        run_instances(instances, {**options, "max_attempts": 4}, start_sandbox=flaky_start, agent_factory=agent_factory)

    records = [json.loads(line) for line in (tmp_path / "out/results.jsonl").read_text().splitlines()]
    [prediction] = [json.loads(line) for line in (tmp_path / "out/predictions.jsonl").read_text().splitlines()]
    assert starts == {"demo__calc-1": 3}
    assert [(r["attempt"], r["error"] is None) for r in records] == [(1, False), (2, False), (3, True)]
    assert "+    return a + b" in prediction["model_patch"]

def test_runner_records_image_provenance(tmp_path):
    def start_sandbox(instance_id, options):
        root = tmp_path / "containers" / instance_id
        _git_repo(root)
        return DockerSandbox(instance_id, runner=FakeDocker(root), timeout_wrapper=False, image="swebench/demo:latest")

    _, agent_factory = _factories(tmp_path, _FixBackend())
    run_instances([{"instance_id": "demo__calc-1", "problem_statement": "p"}], _options(tmp_path),
                  start_sandbox=start_sandbox, agent_factory=agent_factory)

    [record] = [json.loads(line) for line in (tmp_path / "out/results.jsonl").read_text().splitlines()]
    assert record["image"] == {
        "image": "swebench/demo:latest", "image_id": "sha256:feed", "repo_digests": ["swebench/demo@sha256:beef"],
    }


def test_provider_failures_are_told_apart_from_agent_failures():
    def record(error_class, message=""):
        return {"termination_reason": "loop_exception",
                "error_details": [f"Exception stage: llm.chat\nException class: {error_class}\nMessage: {message}"]}

    assert is_provider_failure(record("RateLimitError"))
    assert is_provider_failure(record("TimeoutError", "LLM stream exceeded its idle 120s timeout"))
    assert not is_provider_failure(record("TimeoutError", "LLM stream exceeded its total 900s timeout"))
    assert not is_provider_failure(record("LLMOutputTruncatedError"))
    assert not is_provider_failure({"termination_reason": "max_steps", "error_details": []})


def test_default_agents_in_one_run_share_one_run_state_store(tmp_path):
    from coder_agent.eval import swebench_docker

    first = swebench_docker._shared_run_state_store(tmp_path / "out" / "run_state.db")
    second = swebench_docker._shared_run_state_store(tmp_path / "out" / ".." / "out" / "run_state.db")
    other = swebench_docker._shared_run_state_store(tmp_path / "other" / "run_state.db")
    assert first is second
    assert other is not first


def test_default_sandbox_uses_the_swebench_conda_env_and_utf8_locale(monkeypatch):
    from coder_agent.eval import swebench_docker

    captured = {}
    monkeypatch.setattr(swebench_docker.DockerSandbox, "start", classmethod(lambda cls, image, **kw: captured.update(kw, image=image)))
    swebench_docker._default_start_sandbox(
        "django__django-11490", {"namespace": "swebench", "network": "none", "cpus": 2.0, "memory": "6g"})

    assert captured["image"] == "swebench/sweb.eval.x86_64.django_1776_django-11490:latest"
    assert captured["env"] == {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    assert captured["network"] == "none"


def test_seeded_order_and_stop_after_support_a_budget_checkpoint(tmp_path, monkeypatch):
    from coder_agent.eval import swebench_docker

    ids = [f"demo__calc-{n}" for n in range(1, 9)]
    instances = tmp_path / "instances.jsonl"
    instances.write_text("".join(json.dumps({"instance_id": i, "problem_statement": "p"}) + "\n" for i in ids))
    seen = []

    def fake_run_instances(batch, options, workers=1):
        pending = batch[: options["stop_after"]] if options["stop_after"] is not None else batch
        seen.append([i["instance_id"] for i in pending])
        return {"ran": len(pending)}

    monkeypatch.setattr(swebench_docker, "run_instances", fake_run_instances)
    argv = ["--instances", str(instances), "--output", str(tmp_path / "out"), "--label", "l",
            "--llm-profile", "glm_5", "--order-seed", "7"]
    swebench_docker.main([*argv, "--stop-after", "3"])
    swebench_docker.main(argv)

    manifest = json.loads((tmp_path / "out/manifest.json").read_text())
    assert manifest["order_seed"] == 7 and "stop_after" not in manifest
    assert sorted(manifest["instance_ids"]) == ids and manifest["instance_ids"] != ids
    assert seen[0] == manifest["instance_ids"][:3] and seen[1] == manifest["instance_ids"]
    try:
        swebench_docker.main([*argv[:-1], "8"])
    except SystemExit as exc:
        assert "order_seed" in str(exc)
    else:
        raise AssertionError("a different processing order must not resume into the same run")


def test_stop_after_limits_one_invocation(tmp_path):
    start_sandbox, agent_factory = _factories(tmp_path, _FixBackend())
    instances = [{"instance_id": f"demo__calc-{n}", "problem_statement": "p"} for n in range(1, 4)]
    first = run_instances(instances, {**_options(tmp_path), "stop_after": 2},
                          start_sandbox=start_sandbox, agent_factory=agent_factory)
    second = run_instances(instances, _options(tmp_path), start_sandbox=start_sandbox, agent_factory=agent_factory)
    assert (first["ran"], second["skipped_completed"], second["ran"]) == (2, 2, 1)


def test_rerunning_a_stop_after_invocation_never_goes_past_its_prefix(tmp_path):
    start_sandbox, agent_factory = _factories(tmp_path, _FixBackend())
    instances = [{"instance_id": f"demo__calc-{n}", "problem_statement": "p"} for n in range(1, 5)]

    def checkpoint(n):
        return run_instances(instances, {**_options(tmp_path), "stop_after": n},
                             start_sandbox=start_sandbox, agent_factory=agent_factory)

    assert checkpoint(1)["ran"] == 1  # interrupted after one of the two checkpoint instances
    assert checkpoint(2)["ran"] == 1  # resumes only the rest of the prefix
    rerun = checkpoint(2)  # e.g. after a stopped checkpoint
    assert (rerun["skipped_completed"], rerun["ran"]) == (2, 0)
    ran = [json.loads(line)["instance_id"] for line in (tmp_path / "out" / "predictions.jsonl").open()]
    assert ran == ["demo__calc-1", "demo__calc-2"]
