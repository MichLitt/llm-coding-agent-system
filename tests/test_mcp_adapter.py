from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from coder_agent.tools.mcp import (
    MCPConfigurationError,
    MCPProtocolError,
    MCPServerConfig,
    discover_mcp_tools,
)
from coder_agent.core.agent import Agent
from coder_agent.core.tool_registry import build_tools


_SERVER = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def _config(*args: str, timeout_seconds: float = 1) -> MCPServerConfig:
    return MCPServerConfig(
        server_id="fixture",
        command=sys.executable,
        args=(str(_SERVER), *args),
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_discovers_calls_and_closes_local_stdio_server():
    tools, clients = await discover_mcp_tools([_config()], existing_names={"read_file"})
    assert [tool.name for tool in tools] == ["mcp.fixture.echo"]
    process = clients[0]._process
    assert await tools[0].execute(value="hello") == "hello"
    record = clients[0].audit_records[-1]
    assert record.status == "ok"
    assert len(record.arguments_sha256) == 64

    await clients[0].close()
    assert process is not None and process.returncode is not None


@pytest.mark.asyncio
async def test_rejects_invalid_remote_schema_and_cleans_up_process():
    with pytest.raises(MCPProtocolError, match="invalid input schema"):
        await discover_mcp_tools([_config("--mode", "bad-schema")], existing_names=set())


@pytest.mark.asyncio
async def test_rejects_malformed_or_structured_error_handshake():
    with pytest.raises(MCPProtocolError, match="invalid JSON"):
        await discover_mcp_tools([_config("--mode", "malformed")], existing_names=set())
    with pytest.raises(MCPProtocolError, match="fixture rejection"):
        await discover_mcp_tools([_config("--mode", "rpc-error")], existing_names=set())


@pytest.mark.asyncio
async def test_rejects_name_collision_with_registered_tool():
    with pytest.raises(MCPConfigurationError, match="name collision"):
        await discover_mcp_tools([_config()], existing_names={"mcp.fixture.echo"})


@pytest.mark.asyncio
async def test_timeout_is_returned_as_tool_error_and_audited():
    tools, clients = await discover_mcp_tools(
        [_config("--mode", "hang-call", timeout_seconds=0.05)], existing_names=set()
    )
    try:
        process = clients[0]._process
        result = await tools[0].execute(value="late")
        assert result.startswith("Error: MCP server 'fixture' timed out")
        assert clients[0].audit_records[-1].failure_category == "timeout"
        assert process is not None and process.returncode is not None
    finally:
        await clients[0].close()


@pytest.mark.asyncio
async def test_structured_tool_error_is_preserved_for_the_agent():
    tools, clients = await discover_mcp_tools([_config("--mode", "tool-error")], existing_names=set())
    try:
        assert await tools[0].execute(value="ignored") == "Error: fixture tool failure"
        assert clients[0].audit_records[-1].failure_category == "tool_error"
    finally:
        await clients[0].close()


@pytest.mark.asyncio
async def test_cancelled_call_records_cancellation_and_cleans_up():
    tools, clients = await discover_mcp_tools(
        [_config("--mode", "hang-call", timeout_seconds=10)], existing_names=set()
    )
    task = asyncio.create_task(tools[0].execute(value="late"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clients[0].audit_records[-1].failure_category == "cancelled"
    await clients[0].close()


def test_config_rejects_unsafe_server_id_and_invalid_timeout():
    with pytest.raises(MCPConfigurationError, match="server id"):
        MCPServerConfig.from_mapping({"id": "not valid", "command": "python"})
    with pytest.raises(MCPConfigurationError, match="timeout_seconds"):
        MCPServerConfig.from_mapping({"id": "safe", "command": "python", "timeout_seconds": 0})


class _Client:
    def __init__(self):
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {"content": [], "tool_uses": [{"id": "1", "name": "mcp.fixture.echo", "input": {"value": "ready"}}]}
        return {"content": [{"type": "text", "text": "done"}], "tool_uses": []}


def test_agent_registers_mcp_only_for_the_active_run(tmp_path):
    client = _Client()
    agent = Agent(tools=[], client=client, workspace=tmp_path, mcp_servers=(_config(),))
    result = agent.run("call the configured tool", max_steps=2)

    assert result.success is True
    assert [tool["name"] for tool in client.calls[0]["tools"]] == ["mcp.fixture.echo"]
    assert result.extra["mcp"]["calls"][0]["arguments_sha256"]
    assert agent.tools == []


def test_default_tool_set_remains_unchanged_without_mcp(tmp_path):
    assert [tool.name for tool in build_tools(tmp_path)] == [
        "read_file", "write_file", "patch_file", "list_dir", "run_command", "search_code"
    ]
