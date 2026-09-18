# MCP Adapter Implementation Report — v0.8.0-mcp

Status: locally implemented and verified; not released or benchmark-accepted.

## Scope

This report records PR-0 / Phase A0 of the v0.8.0 plan: a default-off,
local-stdio MCP adapter for the Agent runtime. It is a capability change only;
it makes no quality-improvement claim and does not advance a delivery Gate.

## What changed

- `coder_agent/tools/mcp.py` adds explicit `MCPServerConfig` validation, a
  JSON-RPC-over-stdio client, discovery, stable `mcp.<server_id>.<tool_name>`
  names, JSON-schema checks, allowlists, timeout/cancellation handling, and
  process cleanup.
- `coder_agent/core/agent.py` discovers configured MCP tools at the start of
  one Agent run and removes/terminates them in the run cleanup path. The normal
  tool list is restored before the next run.
- `coder_agent/config.py`, `config.yaml`, and `coder_agent/cli/factory.py`
  define the empty-by-default `tools.mcp_servers` configuration path.
- MCP run metadata contains a server-config SHA256, executable fingerprint,
  discovered schema hash, and per-call argument SHA256/status/duration/failure
  category. It excludes raw environment values, raw arguments, and server
  process stderr.
- `tests/fixtures/fake_mcp_server.py` and `tests/test_mcp_adapter.py` provide
  deterministic local lifecycle coverage. `README.md` documents the opt-in
  configuration and the security boundary.

## Intended behavior and boundary

With `mcp_servers: []` (the checked-in default), no MCP subprocess starts and
the Agent exposes exactly its pre-existing six built-in tools. With an explicit
server configuration, the subprocess receives only `PATH` for executable
resolution plus explicitly allowlisted environment-variable values. The adapter
does not implement remote transports, OAuth, automatic discovery, or automatic
server installation.

On handshake, schema, response, or name-collision failure the run rejects the
MCP integration. On a tool timeout or cancellation it terminates the unhealthy
server rather than reusing a potentially desynchronized stdio stream.

## Verification

- Targeted MCP/config/retrieval tests: `38 passed`.
- Full Agent owner suite: `298 passed` via `uv run pytest`.
- `git diff --check`: passed.

Covered failure paths include malformed response, JSON-RPC error, invalid
schema, tool-name collision, timeout, cancellation, cleanup, and default-off
tool-set preservation.

## Rebaseline decision

A rebaseline is required before any C3/C4/C6 benchmark claim involving this
code version. The repository rule treats changes under `tools/` as behavioral,
even though the checked-in default has MCP disabled and the legacy tool schema
snapshot is unchanged. No baseline was run in this implementation slice; the
v0.8.0 frozen protocol and artifact plan remain the source of truth.

## Remaining work

Phase A0 needs review/merge before it is considered delivered. The following
v0.8.0 work remains separate: ConversationBench schema/runner and fixtures,
ComplexCodeBench, SWE 8-to-12 expansion, frozen baselines, and any MCP-assisted
benchmark lane after the adapter is stable.
