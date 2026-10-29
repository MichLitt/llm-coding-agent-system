"""Shared fixtures."""

import json

import pytest


def _reply(prompt, completion, cached=0):
    usage = {"prompt_tokens": prompt, "completion_tokens": completion, "prompt_tokens_details": {"cached_tokens": cached}}
    call = {"index": 0, "id": "call", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
    return {"role": "assistant", "content": "", "tool_calls": [call], "extra": {"response": {"usage": usage}}}


def _trajectory(directory, instance_id, status, replies, api_calls=None):
    path = directory / instance_id / f"{instance_id}.traj.json"
    path.parent.mkdir(parents=True)
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, *replies]
    info = {"exit_status": status, "model_stats": {"api_calls": len(replies) if api_calls is None else api_calls},
            "mini_version": "2.4.6", "config": {"model": {"model_name": "openai/glm-5.1"}}}
    path.write_text(json.dumps({"info": info, "messages": messages}))


@pytest.fixture
def mini_arm(tmp_path):
    """a resolved; b rate-limited, then retried; c rate-limited twice; d out of steps."""
    run = tmp_path / "R"
    _trajectory(run, "a__x-1", "Submitted", [_reply(100, 10, cached=60), _reply(200, 20)])
    _trajectory(run, "b__x-2", "RateLimitError", [], api_calls=1)
    _trajectory(run, "c__x-3", "RateLimitError", [_reply(100, 5)], api_calls=2)
    _trajectory(run, "d__x-4", "LimitsExceeded", [_reply(50, 5)] * 3)
    (run / "preds.json").write_text(json.dumps({
        "a__x-1": {"model_patch": "diff a"}, "b__x-2": {"model_patch": ""},
        "c__x-3": {"model_patch": ""}, "d__x-4": {"model_patch": ""},
    }))
    retry = run / "retry-1"
    _trajectory(retry, "b__x-2", "Submitted", [_reply(300, 30)])
    _trajectory(retry, "c__x-3", "RateLimitError", [], api_calls=1)
    (retry / "preds.json").write_text(json.dumps({"b__x-2": {"model_patch": "diff b"}, "c__x-3": {"model_patch": ""}}))
    return run
