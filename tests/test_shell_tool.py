"""Tests for RunCommandTool: timeout, output decoding, blocked commands, exit codes."""

import time

import pytest

from coder_agent.tools import shell_tool
from coder_agent.tools.shell_tool import RunCommandTool


@pytest.mark.asyncio
async def test_run_command_times_out_without_hanging():
    tool = RunCommandTool()
    start = time.monotonic()

    with pytest.raises(RuntimeError, match="command timed out"):
        await tool.execute(
            command='python -c "import time; time.sleep(5)"',
            timeout=1,
        )

    elapsed = time.monotonic() - start
    assert elapsed < 4


@pytest.mark.asyncio
async def test_run_command_decodes_invalid_output_without_crashing():
    tool = RunCommandTool()

    result = await tool.execute(
        command='python -c "import sys; sys.stdout.buffer.write(bytes([0xc3, 0x28])); sys.stderr.buffer.write(bytes([0xff]))"',
        timeout=5,
    )

    assert "Exit code: 0" in result
    assert "STDOUT:" in result
    assert "STDERR:" in result


@pytest.mark.asyncio
async def test_run_command_returns_nonzero_exit_code():
    tool = RunCommandTool()

    result = await tool.execute(
        command="python -c 'import sys; sys.exit(42)'",
        timeout=5,
    )

    assert "Exit code: 42" in result


@pytest.mark.asyncio
async def test_run_command_captures_stdout_and_stderr_separately():
    tool = RunCommandTool()

    result = await tool.execute(
        command='python -c "import sys; print(\'out\'); print(\'err\', file=sys.stderr)"',
        timeout=5,
    )

    assert "Exit code: 0" in result
    assert "out" in result
    assert "err" in result


@pytest.mark.asyncio
async def test_run_command_blocked_command_raises_runtime_error(monkeypatch):
    monkeypatch.setattr(shell_tool, "BLOCKED_PATTERNS", ["rm -rf /"])

    tool = RunCommandTool()

    with pytest.raises(RuntimeError, match="command blocked for safety"):
        await tool.execute(command="rm -rf /", timeout=5)


@pytest.mark.asyncio
async def test_run_command_partial_blocked_pattern_in_longer_command(monkeypatch):
    """A blocked pattern embedded in a longer command string is still rejected."""
    monkeypatch.setattr(shell_tool, "BLOCKED_PATTERNS", ["sudo"])

    tool = RunCommandTool()

    with pytest.raises(RuntimeError, match="command blocked for safety"):
        await tool.execute(command="sudo apt-get install vim", timeout=5)


@pytest.mark.asyncio
async def test_run_command_empty_output_is_valid():
    tool = RunCommandTool()

    result = await tool.execute(command="python -c 'pass'", timeout=5)

    assert "Exit code: 0" in result
    assert "STDOUT:" in result


# ---------------------------------------------------------------------------
# python / pytest normalization tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_python_bare_prefix_normalized():
    """'python foo.py' → sys.executable, no 'command not found'."""
    tool = RunCommandTool()
    result = await tool.execute(command="python --version", timeout=5)
    assert "Exit code: 0" in result


@pytest.mark.asyncio
async def test_python_after_and_and_normalized():
    """'cd /tmp && python --version' — python after && must be normalized."""
    tool = RunCommandTool()
    result = await tool.execute(command="cd /tmp && python --version", timeout=5)
    assert "Exit code: 0" in result
    assert "command not found" not in result


@pytest.mark.asyncio
async def test_pytest_bare_prefix_normalized():
    """'pytest --version' → sys.executable -m pytest."""
    tool = RunCommandTool()
    result = await tool.execute(command="pytest --version", timeout=5)
    assert "Exit code: 0" in result


@pytest.mark.asyncio
async def test_pytest_after_and_and_normalized():
    """'cd /tmp && pytest --version' — pytest after && must be normalized."""
    tool = RunCommandTool()
    result = await tool.execute(command="cd /tmp && pytest --version", timeout=5)
    assert "Exit code: 0" in result
    assert "command not found" not in result


@pytest.mark.asyncio
async def test_python3_not_double_normalized():
    """'python3' must NOT be matched and left unchanged."""
    tool = RunCommandTool()
    result = await tool.execute(command="python3 --version", timeout=5)
    assert "Exit code: 0" in result


@pytest.mark.asyncio
async def test_path_prefixed_python_not_normalized():
    """An absolute path like '/path/to/python' must not be rewritten."""
    import asyncio
    import sys
    import unittest.mock as mock

    captured = []
    original_create = asyncio.create_subprocess_shell

    async def fake_create(cmd, **kw):
        captured.append(cmd)
        return await original_create(cmd, **kw)

    with mock.patch("asyncio.create_subprocess_shell", side_effect=fake_create):
        tool = RunCommandTool()
        try:
            await tool.execute(command=f"{sys.executable} --version", timeout=5)
        except Exception:
            pass

    assert captured, "no command captured"
    # The absolute path should be preserved intact (not wrapped in extra quotes)
    assert sys.executable in captured[0]


@pytest.mark.asyncio
async def test_python_m_pytest_not_double_expanded():
    """Bug regression: 'python -m pytest' must NOT become
    '/venv/python -m /venv/python -m pytest' after sequential substitutions.
    The single-pass regex handles 'python -m pytest' atomically."""
    import asyncio
    import unittest.mock as mock

    captured = []
    original_create = asyncio.create_subprocess_shell

    async def fake_create(cmd, **kw):
        captured.append(cmd)
        return await original_create(cmd, **kw)

    with mock.patch("asyncio.create_subprocess_shell", side_effect=fake_create):
        tool = RunCommandTool()
        try:
            await tool.execute(command="python -m pytest --version", timeout=5)
        except Exception:
            pass

    assert captured, "no command captured"
    cmd = captured[0]
    # Must contain exactly one occurrence of "-m pytest", not two
    assert cmd.count("-m pytest") == 1, f"double expansion detected: {cmd!r}"
    # Must not contain the pattern '-m /absolute/path' (module spec error)
    assert "Error while finding module specification" not in cmd
