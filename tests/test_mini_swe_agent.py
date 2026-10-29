"""mini-SWE-agent output (WP1 arm R): records, provider-failure retries and merged predictions."""

import json

from coder_agent.eval.mini_swe_agent import main, merge_predictions, retry_ids, trajectory_records
from coder_agent.eval.swebench_summary import summarize


def test_records_carry_exit_status_attempt_and_provider_usage(mini_arm):
    records = trajectory_records(mini_arm)

    assert [(r["instance_id"], r["attempt"], r["termination_reason"]) for r in records] == [
        ("a__x-1", 1, "Submitted"), ("b__x-2", 1, "RateLimitError"), ("c__x-3", 1, "RateLimitError"),
        ("d__x-4", 1, "LimitsExceeded"), ("b__x-2", 2, "Submitted"), ("c__x-3", 2, "RateLimitError"),
    ]
    first = records[0]
    assert first["provider_error"] is False and first["steps"] == 2 and first["tool_calls"] == 2
    assert (first["agent_version"], first["model_name"]) == ("2.4.6", "openai/glm-5.1")
    assert first["usage"] == {"llm_calls": 2, "input_tokens": 300, "output_tokens": 30, "cached_input_tokens": 60,
                              "token_usage_status": "provider"}
    rate_limited = records[1]
    assert rate_limited["provider_error"] is True
    assert rate_limited["usage"]["input_tokens"] is None and rate_limited["usage"]["token_usage_status"] == "partial"
    assert records[2]["usage"]["token_usage_status"] == "partial"  # one call failed without a reply


def test_only_first_attempt_provider_failures_are_retried(mini_arm):
    assert retry_ids(mini_arm) == ["b__x-2", "c__x-3"]


def test_predictions_take_each_instance_from_its_last_attempt(mini_arm, tmp_path, capsys):
    run = mini_arm
    rows = merge_predictions(run, "wp1-R")
    assert [(r["instance_id"], r["model_patch"]) for r in rows] == [
        ("a__x-1", "diff a"), ("b__x-2", "diff b"), ("c__x-3", ""), ("d__x-4", ""),
    ]
    assert {r["model_name_or_path"] for r in rows} == {"wp1-R"}

    main(["merge-predictions", "--run-dir", str(run), "--label", "wp1-R", "--output", str(tmp_path / "p.jsonl")])
    assert [json.loads(line) for line in (tmp_path / "p.jsonl").read_text().splitlines()] == rows
    main(["retry-ids", "--run-dir", str(run)])
    assert capsys.readouterr().out == "b__x-2\nc__x-3\n"


def test_without_a_retry_predictions_match_the_first_attempt(mini_arm):
    run = mini_arm
    for path in sorted((run / "retry-1").rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    (run / "retry-1").rmdir()
    assert [r["model_patch"] for r in merge_predictions(run, "wp1-R")] == ["diff a", "", "", ""]
    assert all(r["attempt"] == 1 for r in trajectory_records(run))


def test_a_mini_swe_agent_arm_is_classified_from_its_trajectories(mini_arm, tmp_path):
    instances = tmp_path / "instances.jsonl"
    instances.write_text("".join(json.dumps({"instance_id": f"{c}__x-{n}", "problem_statement": "p"}) + "\n"
                                 for n, c in enumerate("abcd", start=1)))
    run = mini_arm
    (run / "harness_report.json").write_text(json.dumps({
        "resolved_ids": ["a__x-1", "b__x-2"], "unresolved_ids": [], "empty_patch_ids": ["c__x-3", "d__x-4"],
    }))

    [arm] = summarize(instances, [("R", run)])["arms"]

    assert {k: v for k, v in arm["outcome_counts"].items() if v} == {"resolved": 2, "provider_error": 1, "empty_patch": 1}
    assert arm["failure_ids"]["provider_error"] == ["c__x-3"]
    assert arm["retried_instances"] == ["b__x-2", "c__x-3"]
    assert arm["termination_by_outcome"] == {
        "LimitsExceeded": {"empty_patch": 1}, "RateLimitError": {"provider_error": 1}, "Submitted": {"resolved": 2},
    }
    assert (arm["usage"]["input_tokens"], arm["usage"]["output_tokens"]) == (750, 75)
    assert arm["steps"]["max"] == 3
