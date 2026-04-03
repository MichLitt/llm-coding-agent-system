"""Tests for agent_errors.py — guidance builders and error classification."""

import pytest

from coder_agent.core.agent_errors import build_import_error_guidance, build_error_guidance


# ---------------------------------------------------------------------------
# Bug A regression: relative imports produce absolute-looking glob patterns
# in Python 3.12, which used to crash build_import_error_guidance().
# ---------------------------------------------------------------------------

def test_relative_import_single_dot_does_not_raise():
    """'.service' → module_path '/service' → workspace.glob('/service.py') must not crash."""
    stderr = "ModuleNotFoundError: No module named '.service'"
    result = build_import_error_guidance(stderr)
    assert isinstance(result, str)
    assert len(result) > 0


def test_relative_import_double_dot_does_not_raise():
    """'..utils' → module_path '//utils' → workspace.glob must not crash."""
    stderr = "ModuleNotFoundError: No module named '..utils'"
    result = build_import_error_guidance(stderr)
    assert isinstance(result, str)


def test_relative_import_dot_models_does_not_raise():
    """'.models' is a common relative import in Django/Flask projects."""
    stderr = "ModuleNotFoundError: No module named '.models'"
    result = build_import_error_guidance(stderr)
    assert isinstance(result, str)


def test_normal_import_still_returns_useful_hint():
    """A plain third-party module name must still produce a helpful hint."""
    stderr = "ModuleNotFoundError: No module named 'partial_queue'"
    result = build_import_error_guidance(stderr)
    assert "partial_queue" in result


def test_dotted_package_import_does_not_raise():
    """'app.models' → module_path 'app/models' → relative pattern, must work fine."""
    stderr = "ModuleNotFoundError: No module named 'app.models'"
    result = build_import_error_guidance(stderr)
    assert isinstance(result, str)


def test_build_error_guidance_import_error_type_does_not_raise():
    """build_error_guidance dispatches to build_import_error_guidance; must not crash
    when the module name starts with a dot (the original guidance_crash bug)."""
    stderr = "ModuleNotFoundError: No module named '.service'"
    result = build_error_guidance("ImportError", stderr)
    assert isinstance(result, str)


def test_build_import_error_guidance_empty_stderr_does_not_raise():
    """Empty stderr should not crash; returns generic fallback hint."""
    result = build_import_error_guidance("")
    assert isinstance(result, str)
