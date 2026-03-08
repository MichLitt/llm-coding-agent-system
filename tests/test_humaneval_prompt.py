from coder_agent.eval.benchmarks.humaneval import HumanEvalBenchmark, HumanEvalTask


def test_humaneval_prompt_enforces_single_syntax_check_then_stop():
    benchmark = HumanEvalBenchmark()
    task = HumanEvalTask(
        task_id="HumanEval/0",
        prompt="def foo():\n    pass\n",
        entry_point="foo",
        test="",
        canonical_solution="",
    )

    prompt = benchmark.build_agent_prompt(task)

    assert "Run `python solution.py` exactly once" in prompt
    assert "stop immediately" in prompt
    assert "Do not run extra verification commands" in prompt
