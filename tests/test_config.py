"""Tests for config validation: validate_config() raises ValueError for invalid values."""

import pytest

from coder_agent.config import Config, validate_config, AgentConfig, ToolsConfig
from coder_agent.cli.factory import (
    make_agent,
    resolve_knowledge_retrieval_policy,
    resolve_model_seed,
)


def _make_config(max_steps=15, max_retries=3, terminal_timeout=30) -> Config:
    cfg = Config()
    cfg.agent = AgentConfig.__new__(AgentConfig)
    cfg.agent.max_steps = max_steps
    cfg.agent.max_retries = max_retries
    cfg.tools = ToolsConfig.__new__(ToolsConfig)
    cfg.tools.terminal_timeout = terminal_timeout
    cfg.tools.blocked_commands = []
    return cfg


def test_valid_config_does_not_raise():
    cfg = _make_config(max_steps=10, max_retries=3, terminal_timeout=30)
    validate_config(cfg)  # should not raise


def test_max_steps_zero_raises_value_error():
    cfg = _make_config(max_steps=0)
    with pytest.raises(ValueError, match="max_steps"):
        validate_config(cfg)


def test_max_steps_negative_raises_value_error():
    cfg = _make_config(max_steps=-5)
    with pytest.raises(ValueError, match="max_steps"):
        validate_config(cfg)


def test_max_retries_negative_raises_value_error():
    cfg = _make_config(max_retries=-1)
    with pytest.raises(ValueError, match="max_retries"):
        validate_config(cfg)


def test_max_retries_zero_is_valid():
    cfg = _make_config(max_retries=0)
    validate_config(cfg)  # zero retries is allowed


def test_terminal_timeout_zero_raises_value_error():
    cfg = _make_config(terminal_timeout=0)
    with pytest.raises(ValueError, match="terminal_timeout"):
        validate_config(cfg)


def test_terminal_timeout_negative_raises_value_error():
    cfg = _make_config(terminal_timeout=-10)
    with pytest.raises(ValueError, match="terminal_timeout"):
        validate_config(cfg)


def test_make_agent_skips_run_state_store_when_disabled(monkeypatch, tmp_path):
    from coder_agent.cli import factory as factory_module

    monkeypatch.setattr(factory_module.cfg.agent, "enable_run_state", False)

    def fail_make_run_state_store(*args, **kwargs):
        raise AssertionError("make_run_state_store should not be called when disabled")

    monkeypatch.setattr(factory_module, "make_run_state_store", fail_make_run_state_store)

    agent = make_agent(no_memory=True, workspace=tmp_path)
    try:
        assert agent.run_state_store is None
    finally:
        agent.close()


def test_run_scoped_retrieval_policy_overrides_preset(monkeypatch, tmp_path):
    from coder_agent.cli import factory as factory_module

    captured = {}
    monkeypatch.setattr(factory_module.cfg.agent, "enable_run_state", False)
    monkeypatch.setattr(
        factory_module,
        "build_tools",
        lambda workspace, *, enable_knowledge_retrieval=None: captured.setdefault(
            "knowledge_retrieval", enable_knowledge_retrieval
        ) or [],
    )

    agent = make_agent(
        {"knowledge_retrieval": True},
        experiment_config={"knowledge_retrieval": False},
        no_memory=True,
        workspace=tmp_path,
    )
    try:
        assert captured["knowledge_retrieval"] is False
    finally:
        agent.close()


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_retrieval_policy_rejects_non_boolean_values(value):
    with pytest.raises(ValueError, match="knowledge_retrieval must be a boolean"):
        resolve_knowledge_retrieval_policy({}, {"knowledge_retrieval": value})


def test_run_scoped_model_seed_is_applied_to_model_config(monkeypatch, tmp_path):
    from coder_agent.cli import factory as factory_module

    monkeypatch.setattr(factory_module.cfg.agent, "enable_run_state", False)
    agent = make_agent(
        experiment_config={"model_seed": 303},
        no_memory=True,
        workspace=tmp_path,
    )
    try:
        assert agent._params()["seed"] == 303
    finally:
        agent.close()


@pytest.mark.parametrize("value", ["303", True, 1.2])
def test_model_seed_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="model_seed must be an integer or null"):
        resolve_model_seed({"model_seed": value})
