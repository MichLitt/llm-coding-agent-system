"""Tests for SearchCodeTool: ripgrep path, regex fallback, file_glob, case sensitivity."""

import shutil

import pytest

from coder_agent.tools import search_tool
from coder_agent.tools.search_tool import SearchCodeTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workspace(tmp_path):
    (tmp_path / "alpha.py").write_text(
        "def hello():\n    return 'Hello World'\n", encoding="utf-8"
    )
    (tmp_path / "beta.py").write_text(
        "def goodbye():\n    return 'Goodbye'\n", encoding="utf-8"
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "gamma.py").write_text("HELLO_CONSTANT = 1\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Basic search (ripgrep if available, otherwise Python regex fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_finds_pattern_in_file(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="hello")

    assert "alpha.py" in result
    assert "hello" in result


@pytest.mark.asyncio
async def test_search_returns_no_matches_for_absent_pattern(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="ZZZNOMATCH")

    assert result == "No matches found."


@pytest.mark.asyncio
async def test_search_case_insensitive_matches_both_cases(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="hello", case_sensitive=False)

    # alpha.py has "hello", sub/gamma.py has "HELLO_CONSTANT"
    assert "alpha.py" in result
    assert "gamma.py" in result


@pytest.mark.asyncio
async def test_search_case_sensitive_does_not_match_wrong_case(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="HELLO", case_sensitive=True)

    # gamma.py has "HELLO_CONSTANT" but alpha.py only has "hello" (lowercase)
    assert "gamma.py" in result
    assert "alpha.py" not in result


@pytest.mark.asyncio
async def test_search_file_glob_restricts_to_matching_files(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    (ws / "readme.txt").write_text("hello from readme", encoding="utf-8")
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="hello", file_glob="*.py")

    assert "alpha.py" in result
    assert "readme.txt" not in result


@pytest.mark.asyncio
async def test_search_respects_max_results(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "many.py").write_text(
        "\n".join(f"target_line_{i}" for i in range(20)), encoding="utf-8"
    )
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="target_line", max_results=5)

    assert result.count("target_line") == 5


@pytest.mark.asyncio
async def test_search_invalid_max_results_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(search_tool, "_WORKSPACE", tmp_path)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="x", max_results=0)

    assert result.startswith("Error")


@pytest.mark.asyncio
async def test_search_path_traversal_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(search_tool, "_WORKSPACE", tmp_path)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="x", path="../outside")

    assert result.startswith("Error: path escapes workspace")


@pytest.mark.asyncio
async def test_search_invalid_regex_returns_error_via_python_fallback(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)
    # Force Python fallback by hiding ripgrep
    monkeypatch.setattr(shutil, "which", lambda _: None)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="[invalid")

    assert result.startswith("Error: invalid regex")


@pytest.mark.asyncio
async def test_search_python_fallback_finds_matches_without_ripgrep(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)
    monkeypatch.setattr(shutil, "which", lambda _: None)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="goodbye")

    assert "beta.py" in result
    assert "goodbye" in result


@pytest.mark.asyncio
async def test_absolute_file_glob_returns_error_not_exception(tmp_path, monkeypatch):
    """Bug C regression: absolute file_glob must not crash with NotImplementedError."""
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)
    # Force Python fallback (no ripgrep) so the rglob() path is exercised.
    monkeypatch.setattr(shutil, "which", lambda _: None)

    tool = SearchCodeTool()
    result = await tool.execute(pattern="def", file_glob="/absolute/path/*.py")

    assert result.startswith("Error")
    assert "relative" in result.lower() or "absolute" in result.lower()


@pytest.mark.asyncio
async def test_absolute_file_glob_with_ripgrep_returns_error(tmp_path, monkeypatch):
    """Absolute file_glob is rejected even when ripgrep is present."""
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(search_tool, "_WORKSPACE", ws)
    # Keep ripgrep available (don't monkeypatch shutil.which).

    tool = SearchCodeTool()
    result = await tool.execute(pattern="def", file_glob="/absolute/path/*.py")

    assert result.startswith("Error")
