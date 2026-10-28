"""SWE-bench summary: outcomes per instance, intervals, paired tests and provenance."""

import hashlib
import json

import pytest

from coder_agent.eval.swebench_summary import (
    exact_mcnemar_p,
    main,
    paired_bootstrap_interval,
    summarize,
    wilson_interval,
)

IDS = ["a__x-1", "b__x-2", "c__x-3", "d__x-4", "e__x-5", "f__x-6"]


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _instances(tmp_path):
    path = tmp_path / "instances.jsonl"
    _write_jsonl(path, [{"instance_id": i, "problem_statement": "p"} for i in IDS])
    return path


def _agent_arm(tmp_path, instances):
    run = tmp_path / "A"
    run.mkdir()
    records = [
        {"instance_id": "a__x-1", "error": None, "termination_reason": "model_stop", "steps": 5, "duration_seconds": 60,
         "usage": {"llm_calls": 5, "input_tokens": 100, "output_tokens": 10, "token_usage_status": "provider"},
         "image": {"image_id": "sha256:1"}},
        {"instance_id": "b__x-2", "error": None, "termination_reason": "max_steps", "steps": 40, "duration_seconds": 900,
         "usage": {"llm_calls": 40, "input_tokens": 900, "output_tokens": 90, "token_usage_status": "provider"}},
        # c: an infrastructure failure, retried once and then resolved.
        {"instance_id": "c__x-3", "error": "RuntimeError: image pull failed", "attempt": 1},
        {"instance_id": "c__x-3", "error": None, "termination_reason": "model_stop", "steps": 7, "duration_seconds": 80,
         "attempt": 2, "usage": {"llm_calls": 7, "input_tokens": 70, "output_tokens": 7, "token_usage_status": "provider"}},
        {"instance_id": "d__x-4", "error": "RuntimeError: image pull failed"},
        {"instance_id": "e__x-5", "error": None, "termination_reason": "loop_exception",
         "error_details": ["Exception stage: llm.chat\nException class: RateLimitError\nMessage: 429"]},
        {"instance_id": "f__x-6", "error": None, "termination_reason": "cancelled", "budget_failure": "wall_cap"},
    ]
    _write_jsonl(run / "results.jsonl", records)
    _write_jsonl(run / "predictions.jsonl", [{"instance_id": i, "model_patch": ""} for i in IDS])
    (run / "manifest.json").write_text(json.dumps({
        "label": "A", "agent_commit": "abc", "llm_profile": "glm_5_nothink",
        "instances_file_sha256": hashlib.sha256(instances.read_bytes()).hexdigest(),
    }))
    (run / "harness_report.json").write_text(json.dumps({
        "resolved_ids": ["a__x-1", "c__x-3"], "unresolved_ids": ["b__x-2"],
        "empty_patch_ids": ["d__x-4", "e__x-5", "f__x-6"], "error_ids": [], "schema_version": 2,
    }))
    return run


def _external_arm(tmp_path):
    run = tmp_path / "R"
    run.mkdir()
    (run / "harness_report.json").write_text(json.dumps({
        "resolved_ids": ["a__x-1", "b__x-2", "e__x-5"], "unresolved_ids": ["c__x-3"], "error_ids": ["d__x-4"],
    }))
    return run


def test_wilson_and_mcnemar_match_known_values():
    low, high = wilson_interval(5, 10)
    assert (round(low, 4), round(high, 4)) == (0.2366, 0.7634)
    assert wilson_interval(0, 0) == (0.0, 0.0)
    assert exact_mcnemar_p(0, 5) == pytest.approx(0.0625)
    assert exact_mcnemar_p(1, 2) == 1.0
    assert exact_mcnemar_p(0, 0) == 1.0


def test_bootstrap_interval_is_deterministic_and_brackets_the_difference():
    first, second = [True] * 30 + [False] * 70, [True] * 20 + [False] * 80
    interval = paired_bootstrap_interval(first, second)
    assert interval == paired_bootstrap_interval(first, second)
    assert interval[0] < 0.10 < interval[1]


def test_every_instance_gets_one_outcome_and_failures_are_listed(tmp_path):
    instances = _instances(tmp_path)
    summary = summarize(instances, [("A", _agent_arm(tmp_path, instances)), ("R", _external_arm(tmp_path))])

    [agent, external] = summary["arms"]
    assert summary["per_instance"]["c__x-3"] == {"A": "resolved", "R": "unresolved"}
    assert agent["resolved"] == 2 and agent["resolved_rate"] == round(2 / 6, 4)
    assert {k: v for k, v in agent["outcome_counts"].items() if v} == {
        "resolved": 2, "unresolved": 1, "infra_error": 1, "provider_error": 1, "wall_cap": 1,
    }
    assert agent["failure_ids"]["infra_error"] == ["d__x-4"]
    assert agent["retried_instances"] == ["c__x-3"]
    assert agent["usage"]["output_tokens"] == 107
    assert agent["provenance"]["manifest"]["agent_commit"] == "abc"
    assert agent["warnings"] == []
    assert external["outcome_counts"]["harness_error"] == 1
    assert external["outcome_counts"]["not_graded"] == 1  # f__x-6 is in no list
    assert external["usage"]["output_tokens"] is None

    [pair] = summary["pairs"]
    assert (pair["both_resolved"], pair["only_first"], pair["only_second"], pair["neither"]) == (1, 1, 2, 2)


def test_cli_writes_json_and_markdown_and_warns_on_a_different_instance_file(tmp_path):
    instances = _instances(tmp_path)
    run = _agent_arm(tmp_path, instances)
    manifest = json.loads((run / "manifest.json").read_text())
    (run / "manifest.json").write_text(json.dumps({**manifest, "instances_file_sha256": "other"}))

    main(["--instances", str(instances), "--arm", f"A={run}", "--output", str(tmp_path / "s.json"),
          "--markdown", str(tmp_path / "s.md")])

    summary = json.loads((tmp_path / "s.json").read_text())
    assert summary["arms"][0]["warnings"] == ["manifest instances_file_sha256 differs from --instances"]
    assert "| A | 2/6 |" in (tmp_path / "s.md").read_text()
