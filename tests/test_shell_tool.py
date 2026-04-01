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
