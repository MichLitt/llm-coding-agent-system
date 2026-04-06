"""Tests for the LLM profile system: resolve_llm_profile() and LLMProfile."""

import os
from unittest.mock import patch

import pytest

from coder_agent.config import LLMProfile, resolve_llm_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROFILES_YAML = {
    "llm": {
        "default_profile": "minimax_m27",
        "profiles": {
            "minimax_m27": {
                "transport": "anthropic",
                "model": "MiniMax-M2.7",
                "api_key_env": "LLM_MINIMAX_M27_API_KEY",
                "base_url_env": "LLM_MINIMAX_M27_BASE_URL",
                "base_url_default": "https://api.minimax.io/anthropic",
            },
            "glm_5": {
                "transport": "openai",
                "model": "glm-5",
                "api_key_env": "LLM_GLM_5_API_KEY",
                "base_url_env": "LLM_GLM_5_BASE_URL",
            },
        },
    }
}


# ---------------------------------------------------------------------------
# resolve_llm_profile — profile resolution
# ---------------------------------------------------------------------------

def test_resolve_known_profile_returns_correct_fields():
    env = {
        "LLM_MINIMAX_M27_API_KEY": "m27-key",
        "LLM_MINIMAX_M27_BASE_URL": "",
    }
    with patch("coder_agent.config._Y", _PROFILES_YAML), patch.dict(os.environ, env, clear=False):
        profile = resolve_llm_profile("minimax_m27")
    assert profile.name == "minimax_m27"
    assert profile.transport == "anthropic"
    assert profile.model == "MiniMax-M2.7"
    assert profile.api_key == "m27-key"
    # base_url_env is empty → falls back to base_url_default
    assert profile.base_url == "https://api.minimax.io/anthropic"


def test_resolve_glm5_profile_openai_transport():
    env = {
        "LLM_GLM_5_API_KEY": "glm-key",
        "LLM_GLM_5_BASE_URL": "https://api.z.ai/api/paas/v4/",
    }
    with patch("coder_agent.config._Y", _PROFILES_YAML), patch.dict(os.environ, env, clear=False):
        profile = resolve_llm_profile("glm_5")
    assert profile.name == "glm_5"
    assert profile.transport == "openai"
    assert profile.model == "glm-5"
    assert profile.api_key == "glm-key"
    assert profile.base_url == "https://api.z.ai/api/paas/v4/"


def test_resolve_default_profile_when_name_is_none():
    env = {"LLM_MINIMAX_M27_API_KEY": "default-key", "LLM_MINIMAX_M27_BASE_URL": ""}
    with patch("coder_agent.config._Y", _PROFILES_YAML), patch.dict(os.environ, env, clear=False):
        profile = resolve_llm_profile(None)
    # default_profile = minimax_m27
    assert profile.name == "minimax_m27"


def test_resolve_unknown_profile_raises_value_error():
    with patch("coder_agent.config._Y", _PROFILES_YAML):
        with pytest.raises(ValueError, match="Unknown LLM profile"):
            resolve_llm_profile("nonexistent_profile")


def test_resolve_unknown_profile_error_lists_available_profiles():
    with patch("coder_agent.config._Y", _PROFILES_YAML):
        with pytest.raises(ValueError, match="minimax_m27"):
            resolve_llm_profile("bad_name")


def test_base_url_env_overrides_default():
    env = {
        "LLM_MINIMAX_M27_API_KEY": "key",
        "LLM_MINIMAX_M27_BASE_URL": "https://custom.example.com/anthropic",
    }
    with patch("coder_agent.config._Y", _PROFILES_YAML), patch.dict(os.environ, env, clear=False):
        profile = resolve_llm_profile("minimax_m27")
    assert profile.base_url == "https://custom.example.com/anthropic"


# ---------------------------------------------------------------------------
# Legacy fallback — no llm.profiles in config
# ---------------------------------------------------------------------------

_LEGACY_YAML = {
    "model": {
        "name": "MiniMax-M2.7",
        "api_format": "anthropic",
    }
}


def test_legacy_fallback_returns_profile_named_legacy():
    env = {
        "ANTHROPIC_API_KEY": "legacy-key",
        "MINIMAX_ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
    }
    with patch("coder_agent.config._Y", _LEGACY_YAML), patch.dict(os.environ, env, clear=False):
        profile = resolve_llm_profile(None)
    assert profile.name == "legacy"
    assert profile.transport == "anthropic"
    assert profile.model == "MiniMax-M2.7"
    assert profile.api_key == "legacy-key"


def test_legacy_fallback_returns_llm_profile_instance():
    with patch("coder_agent.config._Y", _LEGACY_YAML):
        profile = resolve_llm_profile(None)
    assert isinstance(profile, LLMProfile)


# ---------------------------------------------------------------------------
# LLMProfile dataclass
# ---------------------------------------------------------------------------

def test_llm_profile_is_a_dataclass():
    profile = LLMProfile(
        name="test",
        transport="openai",
        model="test-model",
        api_key="k",
        base_url=None,
    )
    assert profile.name == "test"
    assert profile.base_url is None
