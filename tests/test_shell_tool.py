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
