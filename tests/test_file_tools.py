import pytest

from coder_agent.tools import file_tools
from coder_agent.tools.file_tools import ReadFileTool


@pytest.mark.asyncio
async def test_read_file_supports_start_line_and_max_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    tool = ReadFileTool()
    result = await tool.execute(path="notes.txt", start_line=2, max_lines=2)

    assert result == "line2\nline3\n"


@pytest.mark.asyncio
async def test_read_file_supports_min_lines_alias_and_start_line_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    tool = ReadFileTool()

    alias_result = await tool.execute(path="notes.txt", min_lines="3", max_lines=1)
    explicit_result = await tool.execute(path="notes.txt", start_line=2, min_lines="4", max_lines=1)

    assert alias_result == "line3\n"
    assert explicit_result == "line2\n"
