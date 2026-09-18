"""Minimal, local-only MCP stdio adapter.

The adapter is deliberately dependency-free and intentionally narrow: it
speaks JSON-RPC over stdio to explicitly configured local servers.  Remote
transports, automatic discovery, and credential forwarding are out of scope.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from coder_agent.tools.base import Tool


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class MCPConfigurationError(ValueError):
    """Raised when an MCP server declaration is unsafe or malformed."""


class MCPProtocolError(RuntimeError):
    """Raised when a configured server does not follow the expected protocol."""


class MCPTimeoutError(TimeoutError):
    """Raised when a server does not answer before its configured deadline."""


@dataclass(frozen=True)
class MCPServerConfig:
    """Explicit local stdio server configuration.

    ``env`` values are read only for keys named in ``env_allowlist``.  This
    prevents accidentally forwarding the parent process environment, which
    commonly contains provider credentials and CI secrets.
    """

    server_id: str
    command: str
    args: tuple[str, ...] = ()
    workdir: Path | None = None
    tool_allowlist: tuple[str, ...] = ()
    timeout_seconds: float = 15.0
    env_allowlist: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MCPServerConfig":
        server_id = value.get("id", value.get("server_id"))
        command = value.get("command")
        if not isinstance(server_id, str) or not _IDENTIFIER.fullmatch(server_id):
            raise MCPConfigurationError("MCP server id must match [A-Za-z][A-Za-z0-9_-]{0,63}")
        if not isinstance(command, str) or not command.strip():
            raise MCPConfigurationError(f"MCP server {server_id!r} requires a non-empty command")

        def strings(key: str) -> tuple[str, ...]:
            raw = value.get(key, [])
            if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
                raise MCPConfigurationError(f"MCP server {server_id!r} {key} must be a list of non-empty strings")
            return tuple(raw)

        timeout = value.get("timeout_seconds", value.get("per_call_timeout_seconds", 15.0))
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise MCPConfigurationError(f"MCP server {server_id!r} timeout_seconds must be positive")
        workdir = value.get("workdir")
        if workdir is not None and (not isinstance(workdir, str) or not workdir.strip()):
            raise MCPConfigurationError(f"MCP server {server_id!r} workdir must be a non-empty string")
        return cls(
            server_id=server_id,
            command=command,
            args=strings("args"),
            workdir=Path(workdir).expanduser().resolve() if workdir else None,
            tool_allowlist=strings("tool_allowlist"),
            timeout_seconds=float(timeout),
            env_allowlist=strings("env_allowlist"),
        )


@dataclass(frozen=True)
class MCPAuditRecord:
    server_id: str
    tool_name: str
    arguments_sha256: str
    status: str
    duration_ms: int
    failure_category: str | None = None


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class MCPStdioClient:
    """One server process for one Agent run, serialized over JSON lines."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._lock = asyncio.Lock()
        self._aborted = False
        self.audit_records: list[MCPAuditRecord] = []
        self.discovery_record: dict[str, str] | None = None

    async def start(self) -> None:
        if self._aborted:
            raise MCPProtocolError(f"MCP server {self.config.server_id!r} is unavailable after a prior lifecycle failure")
        if self._process is not None:
            return
        env = {"PATH": os.defpath}
        for key in self.config.env_allowlist:
            if key in os.environ:
                env[key] = os.environ[key]
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self.config.workdir) if self.config.workdir else None,
                env=env,
            )
        except OSError as exc:
            raise MCPProtocolError(f"could not start MCP server {self.config.server_id!r}: {exc}") from exc

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        await self.start()
        assert self._process is not None and self._process.stdin is not None and self._process.stdout is not None
        async with self._lock:
            request_id = self._next_id
            self._next_id += 1
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})}
            self._process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
            await self._process.stdin.drain()
            try:
                line = await asyncio.wait_for(self._process.stdout.readline(), timeout=self.config.timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise MCPTimeoutError(f"MCP server {self.config.server_id!r} timed out during {method}") from exc
            if not line:
                raise MCPProtocolError(f"MCP server {self.config.server_id!r} closed stdout during {method}")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MCPProtocolError(f"MCP server {self.config.server_id!r} returned invalid JSON") from exc
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise MCPProtocolError(f"MCP server {self.config.server_id!r} returned an invalid JSON-RPC response")
            if "error" in response:
                error = response["error"]
                detail = error.get("message", "unknown error") if isinstance(error, dict) else "unknown error"
                raise MCPProtocolError(f"MCP server {self.config.server_id!r} rejected {method}: {detail}")
            if "result" not in response:
                raise MCPProtocolError(f"MCP server {self.config.server_id!r} returned no result for {method}")
            return response["result"]

    async def initialize_and_list_tools(self) -> list[dict[str, Any]]:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "coder-agent", "version": "0.8.0"},
            },
        )
        if not isinstance(result, dict):
            raise MCPProtocolError(f"MCP server {self.config.server_id!r} returned invalid initialize result")
        # The initialized notification has no response by protocol design.
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n')
        await self._process.stdin.drain()
        tools_result = await self.request("tools/list")
        if not isinstance(tools_result, dict) or not isinstance(tools_result.get("tools"), list):
            raise MCPProtocolError(f"MCP server {self.config.server_id!r} returned invalid tools/list result")
        tools = tools_result["tools"]
        self.discovery_record = {
            "server_id": self.config.server_id,
            "server_config_sha256": _sha256_json({
                "id": self.config.server_id,
                "command": self.config.command,
                "args": self.config.args,
                "workdir": str(self.config.workdir) if self.config.workdir else None,
                "tool_allowlist": self.config.tool_allowlist,
                "timeout_seconds": self.config.timeout_seconds,
                "env_allowlist": self.config.env_allowlist,
            }),
            "executable_fingerprint": _executable_fingerprint(self.config.command),
            "tool_schema_sha256": _sha256_json(tools),
        }
        return tools

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def abort(self) -> None:
        """Stop an unhealthy process; do not reuse a timed-out protocol stream."""
        self._aborted = True
        await self.close()


class MCPTool(Tool):
    def __init__(self, *, client: MCPStdioClient, server_tool_name: str, description: str, input_schema: dict[str, Any]):
        self.client = client
        self.server_tool_name = server_tool_name
        super().__init__(
            name=f"mcp.{client.config.server_id}.{server_tool_name}",
            description=description,
            input_schema=input_schema,
        )

    async def execute(self, **kwargs: Any) -> str:
        started_at = time.perf_counter()
        status, failure_category = "ok", None
        try:
            result = await self.client.request("tools/call", {"name": self.server_tool_name, "arguments": kwargs})
            rendered = _format_tool_result(result)
            if rendered.startswith("Error:"):
                status, failure_category = "error", "tool_error"
            return rendered
        except MCPTimeoutError as exc:
            status, failure_category = "error", "timeout"
            await self.client.abort()
            return f"Error: {exc}"
        except asyncio.CancelledError:
            status, failure_category = "error", "cancelled"
            await self.client.abort()
            raise
        except MCPProtocolError as exc:
            status, failure_category = "error", "protocol"
            return f"Error: {exc}"
        finally:
            self.client.audit_records.append(
                MCPAuditRecord(
                    server_id=self.client.config.server_id,
                    tool_name=self.server_tool_name,
                    arguments_sha256=_sha256_json(kwargs),
                    status=status,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                    failure_category=failure_category,
                )
            )


def _format_tool_result(result: Any) -> str:
    if not isinstance(result, dict):
        raise MCPProtocolError("MCP tools/call result must be an object")
    content = result.get("content", [])
    if not isinstance(content, list):
        raise MCPProtocolError("MCP tools/call content must be a list")
    text_parts = [item["text"] for item in content if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)]
    rendered = "\n".join(text_parts) if text_parts else json.dumps(content, ensure_ascii=False, sort_keys=True)
    return f"Error: {rendered}" if result.get("isError") is True else rendered


def _executable_fingerprint(command: str) -> str:
    """Identify the configured executable without recording its environment."""
    path = Path(command)
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"sha256:{digest}"
    return f"command:{command}"


async def discover_mcp_tools(
    configs: Sequence[MCPServerConfig],
    *,
    existing_names: set[str],
) -> tuple[list[MCPTool], list[MCPStdioClient]]:
    """Start configured servers, validate their schemas, and expose allowlisted tools."""
    tools: list[MCPTool] = []
    clients: list[MCPStdioClient] = []
    seen_names = set(existing_names)
    try:
        for config in configs:
            client = MCPStdioClient(config)
            clients.append(client)
            for descriptor in await client.initialize_and_list_tools():
                if not isinstance(descriptor, dict):
                    raise MCPProtocolError(f"MCP server {config.server_id!r} returned a non-object tool descriptor")
                name = descriptor.get("name")
                schema = descriptor.get("inputSchema")
                if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
                    raise MCPProtocolError(f"MCP server {config.server_id!r} returned an invalid tool name")
                if config.tool_allowlist and name not in config.tool_allowlist:
                    continue
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise MCPProtocolError(f"MCP server {config.server_id!r} returned invalid input schema for {name!r}")
                tool_name = f"mcp.{config.server_id}.{name}"
                if tool_name in seen_names:
                    raise MCPConfigurationError(f"MCP tool name collision: {tool_name}")
                seen_names.add(tool_name)
                tools.append(MCPTool(
                    client=client,
                    server_tool_name=name,
                    description=str(descriptor.get("description") or f"MCP tool {name}"),
                    input_schema=schema,
                ))
        return tools, clients
    except BaseException:
        await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
        raise
