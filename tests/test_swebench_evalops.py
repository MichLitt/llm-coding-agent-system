"""EvalOps agent/v1 aggregate reports for SWE-bench arms (WP1 §8)."""

import json
import sqlite3

import pytest

from coder_agent.eval.swebench_evalops import BENCHMARK_NAME, build_report, main
from coder_agent.eval.swebench_summary import summarize

IDS = ["a__x-1", "b__x-2", "c__x-3", "d__x-4"]


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _usage(input_tokens, output_tokens):
    return {"llm_calls": 2, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cached_input_tokens": 0, "token_usage_status": "provider"}


def agent_arm(tmp_path):
    """a, b resolved; c an infrastructure failure retried into an unresolved run; d unresolved."""
    run = tmp_path / "A"
    run.mkdir()
    _write_jsonl(run / "results.jsonl", [
        {"instance_id": "a__x-1", "run_id": "r-a", "steps": 5, "duration_seconds": 60.0, "usage": _usage(100, 10)},
        {"instance_id": "b__x-2", "run_id": "r-b", "steps": 7, "duration_seconds": 30.0, "usage": _usage(200, 20)},
        {"instance_id": "c__x-3", "error": "RuntimeError: image pull failed", "duration_seconds": 1.0, "attempt": 1},
        {"instance_id": "c__x-3", "run_id": "r-c", "steps": 3, "duration_seconds": 90.0, "attempt": 2,
         "usage": _usage(300, 30)},
        {"instance_id": "d__x-4", "run_id": "r-d", "steps": 1, "duration_seconds": 20.0, "usage": _usage(400, 40)},
    ])
    with sqlite3.connect(run / "run_state.db") as db:
        db.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, total_tool_calls INTEGER, tool_success_rate REAL)")
        db.executemany("INSERT INTO runs VALUES (?, ?, ?)",
                       [("r-a", 4, 1.0), ("r-b", 6, 0.5), ("r-c", 0, None), ("r-d", 10, 0.9)])
    (run / "manifest.json").write_text(json.dumps({
        "label": "A", "agent_commit": "abc1234", "preset": "C3", "llm_profile": "glm_5_nothink",
    }))
    (run / "harness_report.json").write_text(json.dumps({
        "resolved_ids": ["a__x-1", "b__x-2"], "unresolved_ids": ["c__x-3", "d__x-4"],
    }))
    return run


@pytest.fixture
def summary(tmp_path, mini_arm):
    instances = tmp_path / "instances.jsonl"
    _write_jsonl(instances, [{"instance_id": i, "problem_statement": "p"} for i in IDS])
    (mini_arm / "harness_report.json").write_text(json.dumps({
        "resolved_ids": ["a__x-1", "b__x-2"], "empty_patch_ids": ["c__x-3", "d__x-4"],
    }))
    return summarize(instances, [("A", agent_arm(tmp_path)), ("R", mini_arm)])


def test_agent_arm_report_sums_last_attempts(tmp_path, summary):
    report = build_report("A", tmp_path / "A", summary)

    assert report["run_id"] == "wp1-formal-A-aggregate-v1"
    assert (report["run_type"], report["status"], report["benchmark_name"]) == ("eval", "completed", BENCHMARK_NAME)
    assert report["task_ids"] == IDS
    assert report["resolved_rate"] == 0.5
    assert (report["total_steps"], report["total_tool_calls"]) == (16, 20)
    assert report["tool_success_rate"] == pytest.approx((4 * 1.0 + 6 * 0.5 + 10 * 0.9) / 20)
    assert (report["input_tokens"], report["output_tokens"], report["total_tokens"]) == (1000, 100, 1100)
    assert (report["llm_call_count"], report["token_usage_status"]) == (8, "provider")
    assert report["wall_duration_ms"] == 200_000  # the failed first attempt of c is not counted
    assert (report["wall_latency_p50_ms"], report["wall_latency_p95_ms"]) == (30_000, 90_000)
    assert (report["preset"], report["llm_profile"], report["git_commit"]) == ("C3", "glm_5_nothink", "abc1234")


def test_mini_swe_agent_report_leaves_untimed_and_incomparable_fields_null(tmp_path, summary):
    report = build_report("R", tmp_path / "R", summary)

    assert report["resolved_rate"] == 0.5 and report["task_ids"] == IDS
    assert (report["total_steps"], report["total_tool_calls"]) == (6, 6)
    assert report["tool_success_rate"] is None
    assert report["wall_duration_ms"] is None
    assert report["wall_latency_p50_ms"] is None and report["wall_latency_p95_ms"] is None
    assert report["token_usage_status"] == "partial"
    assert (report["preset"], report["llm_profile"], report["git_commit"]) == (
        "mini-swe-agent-2.4.6", "openai/glm-5.1", None)


def test_cli_writes_one_report_per_arm(tmp_path, summary):
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    main(["--summary", str(tmp_path / "summary.json"), "--arm", f"A={tmp_path / 'A'}",
          "--arm", f"R={tmp_path / 'R'}", "--output-dir", str(tmp_path / "out")])

    written = json.loads((tmp_path / "out" / "A.agent-v1.json").read_text())
    assert written == build_report("A", tmp_path / "A", summary)
    assert (tmp_path / "out" / "R.agent-v1.json").exists()


def test_a_run_missing_from_the_run_state_store_is_an_error(tmp_path, summary):
    with sqlite3.connect(tmp_path / "A" / "run_state.db") as db:
        db.execute("DELETE FROM runs WHERE run_id = 'r-d'")
    with pytest.raises(SystemExit, match="missing runs"):
        build_report("A", tmp_path / "A", summary)
