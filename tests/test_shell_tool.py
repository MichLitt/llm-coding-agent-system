import time

import pytest

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
