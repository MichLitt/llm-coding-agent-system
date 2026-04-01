"""Tests for config validation: validate_config() raises ValueError for invalid values."""

import pytest

from coder_agent.config import Config, validate_config, AgentConfig, ToolsConfig


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
