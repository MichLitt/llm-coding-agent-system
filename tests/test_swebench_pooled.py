"""Pooled comparison over seeds: per-instance scores, exact sign-flip test, bootstrap interval."""

import json

import pytest

from coder_agent.eval.swebench_pooled import main, pooled_comparison, sign_flip_p

# Two seeds per configuration; R = resolved, U = unresolved, E = empty patch.
PER_INSTANCE = {
    "a__x-1": {"A-1": "resolved", "A-2": "resolved", "C-1": "resolved", "C-2": "resolved"},
    "b__x-2": {"A-1": "resolved", "A-2": "unresolved", "C-1": "resolved", "C-2": "resolved"},
    "c__x-3": {"A-1": "unresolved", "A-2": "unresolved", "C-1": "resolved", "C-2": "unresolved"},
    "d__x-4": {"A-1": "unresolved", "A-2": "empty_patch", "C-1": "unresolved", "C-2": "unresolved"},
}


def test_sign_flip_p_is_exact():
    assert sign_flip_p([0, 0]) == 1.0
    # +3 or -3 out of eight equally likely sign patterns.
    assert sign_flip_p([1, 1, 1]) == 0.25
    # Sums 3, 1, -1, -3: only the observed extreme pair is as large.
    assert sign_flip_p([2, 1]) == 0.5
    assert sign_flip_p([-1, -1, -1]) == 0.25


def test_pooled_comparison_scores_each_instance_over_its_seeds():
    result = pooled_comparison(PER_INSTANCE, ["A-1", "A-2"], ["C-1", "C-2"])

    assert (result["baseline_resolved_runs"], result["candidate_resolved_runs"]) == (3, 5)
    assert result["rate_difference"] == 0.25
    assert result["score_differences"] == {"0": 2, "1": 2}
    assert result["sign_flip_p"] == 0.5
    assert result["by_baseline_score"] == {
        "0": {"instances": 2, "candidate_resolved_runs": 1},
        "1": {"instances": 1, "candidate_resolved_runs": 2},
        "2": {"instances": 1, "candidate_resolved_runs": 2},
    }
    assert result["resolved_in_every_run"] == {"baseline": 1, "candidate": 2}
    assert result["outcomes"]["A-2"] == {"empty_patch": 1, "resolved": 1, "unresolved": 2}
    low, high = result["rate_difference_bootstrap95"]
    assert 0.0 <= low <= result["rate_difference"] <= high <= 0.5


def test_pooled_comparison_rejects_unmatched_or_missing_arms():
    with pytest.raises(ValueError, match="same number of arms"):
        pooled_comparison(PER_INSTANCE, ["A-1", "A-2"], ["C-1"])
    with pytest.raises(ValueError, match="missing"):
        pooled_comparison(PER_INSTANCE, ["A-1"], ["B-1"])


def test_cli_reads_a_summary_and_writes_the_comparison(tmp_path, capsys):
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"schema_version": "swebench-summary/v1", "per_instance": PER_INSTANCE}))
    output = tmp_path / "out" / "pooled.json"

    assert main(["--summary", str(summary), "--baseline", "A-1,A-2", "--candidate", "C-1,C-2",
                 "--output", str(output)]) == 0

    written = json.loads(output.read_text())
    assert written["candidate_resolved_runs"] == 5
    assert json.loads(capsys.readouterr().out) == written
