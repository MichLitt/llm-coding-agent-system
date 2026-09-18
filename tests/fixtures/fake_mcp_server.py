"""Deterministic JSON-lines MCP server used only by adapter tests."""

from __future__ import annotations

import argparse
import json
import sys
import time


parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("normal", "bad-schema", "hang-call", "malformed", "rpc-error", "tool-error"), default="normal")
args = parser.parse_args()

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        continue
    if args.mode == "malformed":
        print("not-json", flush=True)
        continue
    if args.mode == "rpc-error":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"message": "fixture rejection"}}), flush=True)
        continue
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        schema = {"type": "string"} if args.mode == "bad-schema" else {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }
        result = {"tools": [{"name": "echo", "description": "Echo a value", "inputSchema": schema}]}
    elif method == "tools/call":
        if args.mode == "hang-call":
            time.sleep(5)
        if args.mode == "tool-error":
            result = {"content": [{"type": "text", "text": "fixture tool failure"}], "isError": True}
        else:
            result = {"content": [{"type": "text", "text": str(request.get("params", {}).get("arguments", {}).get("value", ""))}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"message": "unknown method"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
