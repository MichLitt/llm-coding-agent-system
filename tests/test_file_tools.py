import pytest

from coder_agent.tools import file_tools
from coder_agent.tools.file_tools import ListDirTool, ReadFileTool, WriteFileTool


# ---------------------------------------------------------------------------
# ReadFileTool (existing tests preserved)
# ---------------------------------------------------------------------------

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


@pytest.mark.asyncio
async def test_read_file_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)

    tool = ReadFileTool()
    result = await tool.execute(path="../outside.txt")

    assert result.startswith("Error: path escapes workspace")


@pytest.mark.asyncio
async def test_read_file_returns_error_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)

    tool = ReadFileTool()
    result = await tool.execute(path="nonexistent.txt")

    assert "Error" in result
    assert "nonexistent.txt" in result


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_file_creates_new_file(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)

    tool = WriteFileTool()
    result = await tool.execute(operation="write", path="new_file.py", content="x = 1\n")

    assert "Written" in result
    assert (tmp_path / "new_file.py").read_text(encoding="utf-8") == "x = 1\n"


@pytest.mark.asyncio
async def test_write_file_overwrites_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)
    (tmp_path / "existing.py").write_text("old content\n", encoding="utf-8")

    tool = WriteFileTool()
    await tool.execute(operation="write", path="existing.py", content="new content\n")

    assert (tmp_path / "existing.py").read_text(encoding="utf-8") == "new content\n"


@pytest.mark.asyncio
async def test_write_file_creates_parent_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)

    tool = WriteFileTool()
    result = await tool.execute(
        operation="write", path="subdir/nested/file.py", content="pass\n"
    )

    assert "Written" in result
    assert (tmp_path / "subdir" / "nested" / "file.py").exists()


@pytest.mark.asyncio
async def test_write_file_edit_replaces_old_text(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)
    (tmp_path / "code.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    tool = WriteFileTool()
    result = await tool.execute(
        operation="edit",
        path="code.py",
        old_text="return 1",
        new_text="return 42",
    )

    assert "Edited" in result
    assert "return 42" in (tmp_path / "code.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_write_file_edit_returns_error_when_old_text_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)
    (tmp_path / "code.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    tool = WriteFileTool()
    result = await tool.execute(
        operation="edit",
        path="code.py",
        old_text="DOES NOT EXIST",
        new_text="something",
    )

    assert result.startswith("Error: old_text not found")


@pytest.mark.asyncio
async def test_write_file_edit_returns_error_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)

    tool = WriteFileTool()
    result = await tool.execute(
        operation="edit",
        path="missing.py",
        old_text="x",
        new_text="y",
    )

    assert result.startswith("Error")


@pytest.mark.asyncio
async def test_write_file_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)

    tool = WriteFileTool()
    result = await tool.execute(operation="write", path="../escape.py", content="bad")

    assert result.startswith("Error: path escapes workspace")


@pytest.mark.asyncio
async def test_write_file_edit_requires_old_text(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)
    (tmp_path / "code.py").write_text("x = 1\n", encoding="utf-8")

    tool = WriteFileTool()
    result = await tool.execute(operation="edit", path="code.py", old_text="", new_text="y")

    assert "old_text is required" in result


# ---------------------------------------------------------------------------
# ListDirTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_dir_shows_files_and_subdirs(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)
    (tmp_path / "file_a.py").write_text("", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "file_b.py").write_text("", encoding="utf-8")

    tool = ListDirTool()
    result = await tool.execute(path=".", depth=1)

    assert "file_a.py" in result
    assert "subdir/" in result


@pytest.mark.asyncio
async def test_list_dir_depth_two_shows_nested_files(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("", encoding="utf-8")

    tool = ListDirTool()
    result = await tool.execute(path=".", depth=2)

    assert "module.py" in result


@pytest.mark.asyncio
async def test_list_dir_returns_error_for_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)

    tool = ListDirTool()
    result = await tool.execute(path="../outside")

    assert result.startswith("Error: path escapes workspace")


@pytest.mark.asyncio
async def test_list_dir_returns_error_for_nonexistent_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(file_tools, "_WORKSPACE", tmp_path)

    tool = ListDirTool()
    result = await tool.execute(path="does_not_exist")

    assert "Error" in result
