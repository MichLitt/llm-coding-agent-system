"""SWE-bench budget checkpoint: projection and verdicts."""

import json

from coder_agent.eval.swebench_budget import main, project


def _results(run_dir, usages):
    run_dir.mkdir(parents=True)
    rows = [{"instance_id": f"x__y-{n}", "usage": usage} for n, usage in enumerate(usages)]
    rows.append({"instance_id": "x__y-infra", "error": "RuntimeError: image pull failed"})
    (run_dir / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def _usage(input_tokens, output_tokens):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "token_usage_status": "provider"}


def test_projection_uses_mean_usage_of_measured_instances(tmp_path):
    _results(tmp_path / "A", [_usage(40_000, 1_000), _usage(80_000, 3_000)])
    projection = project(tmp_path / "A", 300)
    assert projection["instances_measured"] == 2
    assert projection["projected_input_tokens"] == 18_000_000
    assert projection["projected_output_tokens"] == 600_000


def test_verdicts_and_exit_codes(tmp_path):
    _results(tmp_path / "ok", [_usage(60_000, 1_500)] * 25)
    _results(tmp_path / "over", [_usage(160_000, 1_500)] * 25)
    _results(tmp_path / "few", [_usage(60_000, 1_500)] * 10)
    caps = ["--instance-runs", "300", "--max-input-tokens", "45000000", "--max-output-tokens", "1000000"]
    assert main(["--run-dir", str(tmp_path / "ok"), *caps]) == 0
    assert main(["--run-dir", str(tmp_path / "over"), *caps]) == 1
    assert main(["--run-dir", str(tmp_path / "few"), *caps]) == 2
