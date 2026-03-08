from coder_agent.eval.analysis import _is_context_lost


def test_is_context_lost_handles_terminal_steps_without_action():
    trajectory = {
        "steps": [
            {"action": {"tool": "write_file", "args": {"path": "solution.py"}}},
            {"action": None},
        ]
    }

    assert _is_context_lost(trajectory) is False
