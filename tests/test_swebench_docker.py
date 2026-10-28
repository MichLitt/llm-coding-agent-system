"""SWE-bench Docker runner: predictions, records, resume and failure isolation (fake Docker)."""

import json
import subprocess
from pathlib import Path

from coder_agent.config import LLMProfile
from coder_agent.core.agent import Agent
from coder_agent.core.agent_types import ModelConfig
from coder_agent.core.llm_client import LLMClient
from coder_agent.eval.swebench_docker import PROMPT_TEMPLATE, load_instances, run_instances
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
    assert summary == {"skipped_completed": 1, "ran": 1, "predictions": str(tmp_path / "out/predictions.jsonl")}
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
